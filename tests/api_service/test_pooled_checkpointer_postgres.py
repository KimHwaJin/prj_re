"""Actual pool concurrency and cancellation without a fragile wall-time target."""

import asyncio
from uuid import uuid4

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool
from dtest.agent_service.runtime.langgraph.checkpointer import (
    create_checkpointer,
)
from tests.api_service.test_user_identity_postgres import database_url, harness
from tests.api_service.test_async_llm_postgres import checkpoint_url


@pytest.mark.asyncio
async def test_operations_use_bounded_shared_pool_and_return_connections(
    harness, database_url, monkeypatch
):
    entered = asyncio.Event()
    release = asyncio.Event()
    active = peak = 0
    connections = set()

    async def blocked_read(self, config):
        nonlocal active, peak
        async with self._cursor() as cur:
            await cur.execute("SELECT 1")
            assert (await cur.fetchone())["?column?"] == 1
            connections.add(cur.connection.info.backend_pid)
            active += 1
            peak = max(peak, active)
            if active == 2:
                entered.set()
            try:
                await release.wait()
            finally:
                active -= 1
        return None

    monkeypatch.setattr(AsyncPostgresSaver, "aget_tuple", blocked_read)
    async with create_checkpointer(
        checkpoint_url(database_url),
        setup_on_start=True,
        min_size=2,
        max_size=2,
    ) as saver:
        assert isinstance(saver.conn, AsyncConnectionPool)
        tasks = [
            asyncio.create_task(
                saver.aget_tuple({"configurable": {"thread_id": str(uuid4())}})
            )
            for _ in range(4)
        ]
        try:
            await asyncio.wait_for(entered.wait(), 5)
            assert peak == 2 and saver.conn.get_stats()["pool_size"] == 2
            assert len(connections) == 2
        finally:
            release.set()
            await asyncio.gather(*tasks)
        assert (
            active == 0
            and peak == 2
            and saver.conn.get_stats()["pool_available"] == 2
        )


@pytest.mark.asyncio
async def test_cancelled_operation_releases_its_pool_connection(
    harness, database_url, monkeypatch
):
    entered = asyncio.Event()

    async def blocked_read(self, config):
        async with self._cursor() as cur:
            await cur.execute("SELECT 1")
            entered.set()
            await asyncio.Event().wait()

    monkeypatch.setattr(AsyncPostgresSaver, "aget_tuple", blocked_read)
    async with create_checkpointer(
        checkpoint_url(database_url),
        setup_on_start=True,
        min_size=1,
        max_size=1,
    ) as saver:
        task = asyncio.create_task(
            saver.aget_tuple({"configurable": {"thread_id": str(uuid4())}})
        )
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert saver.conn.get_stats()["pool_available"] == 1
        async with saver.conn.connection() as conn:
            assert (await (await conn.execute("SELECT 1")).fetchone())[
                "?column?"
            ] == 1


@pytest.mark.asyncio
async def test_repair_candidate_survives_pool_restart_and_keeps_receipts(
    harness, database_url, tmp_path, monkeypatch
):
    from tests.agent_service import test_agentic_repair as fixture
    from dtest.agent_service.agents.analysis.planning.graph import (
        build_planning_graph,
    )
    from dtest.application.runs.graph_invocation import GraphInvocation
    from langgraph.types import Command
    from dtest.contracts.events import EventContext, ExecutorEvent
    from dtest.contracts.user_resume import resume_identity, resume_envelope
    from uuid import UUID

    dsn = checkpoint_url(database_url)
    async with create_checkpointer(
        dsn, setup_on_start=True, min_size=1, max_size=2
    ) as saver:
        monkeypatch.setattr(fixture, "InMemorySaver", lambda: saver)
        (
            runtime,
            executor,
            graph,
            config,
            state,
            calls,
            deliver,
            resume,
        ) = await fixture.scenario(tmp_path, monkeypatch, needs_input=True)
        original = state["approved_snapshot"]
        ctx, state = await deliver(executor.events[0])
        candidate = state["repair_candidate"]
        assert (
            state["repair_review"]
            and len(calls) == 1
            and len(executor.calls) == 1
        )
    async with create_checkpointer(
        dsn, setup_on_start=False, min_size=1, max_size=2
    ) as saver:
        graph = build_planning_graph(runtime, checkpointer=saver)
        snapshot = await graph.aget_state(config)
        assert snapshot.values["repair_candidate"] == candidate
        boundary = snapshot.tasks[0].interrupts[0]
        command = fixture.approve(snapshot.values["repair_review"])
        identity = resume_identity(str(uuid4()), boundary.id, command)
        state = await graph.ainvoke(
            Command(resume={boundary.id: resume_envelope(identity, command)}),
            config,
            durability="sync",
        )
        assert (
            state["repair_attempts"] == 1
            and state["approved_snapshot"] == original
        )
        assert (
            len(executor.calls) == 2
            and executor.globals["repair_load_calls"] == 1
        )

        async def event(value):
            context = EventContext(
                namespace="test",
                session_id=ctx.session_id,
                task_id=ctx.task_id,
                execution_id=UUID(executor.id),
                command_id=uuid4(),
                event=ExecutorEvent.model_validate(value),
            )
            await GraphInvocation(graph, model_validator=None).executor_resume(
                context
            )
            return context

        completed = await event(executor.events[1])
        assert executor.calls[-1][0].endswith("/finalize")
        before = len(executor.calls)
        await GraphInvocation(graph, model_validator=None).executor_resume(
            completed
        )
        assert len(executor.calls) == before
        await event(
            executor.event(
                "execution.completed", {"status": "SUCCEEDED", "error": None}
            )
        )
        final = await graph.aget_state(config)
        assert (
            not final.next
            and final.values["final_response"]["repair"]["attempts"] == 1
        )
        assert final.values["final_response"]["status"] == "analysis_completed"
        assert (
            len([s async for s in graph.aget_state_history(config, limit=2)])
            == 2
        )
        assert (
            saver.conn.get_stats()["pool_available"]
            == saver.conn.get_stats()["pool_size"]
        )
    import os

    output = os.environ.get("DTEST_REPAIR_CHECKPOINT_OUTPUT")
    if output:
        from pathlib import Path
        import json, sys

        target = Path(output).resolve()
        assert target.is_relative_to(Path("/private/tmp"))
        sys.path.insert(
            0,
            str(
                Path(__file__).resolve().parents[2]
                / "scripts/benchmarks/worker_e2e"
            ),
        )
        from checkpoint_profile import capture

        target.write_text(
            json.dumps(
                capture(dsn, [config["configurable"]["thread_id"]]),
                ensure_ascii=False,
                indent=2,
            )
        )


@pytest.mark.asyncio
async def test_early_history_close_returns_single_pool_connection(
    harness, database_url
):
    from tests.api_service.test_graph_runtime_postgres import checkpoint_graph
    from langgraph.types import Command

    dsn = checkpoint_url(database_url)
    config = {"configurable": {"thread_id": str(uuid4())}}
    async with create_checkpointer(
        dsn, setup_on_start=True, min_size=1, max_size=1
    ) as saver:
        graph = checkpoint_graph(saver)
        await graph.ainvoke(
            {"label": "history close"}, config, durability="sync"
        )
        await graph.ainvoke(
            Command(resume="approved"), config, durability="sync"
        )
        history = saver.alist(config, limit=2)
        try:
            assert await anext(history)
            assert saver.conn.get_stats()["pool_available"] == 0
        finally:
            await history.aclose()
        assert saver.conn.get_stats()["pool_available"] == 1
        assert await saver.aget_tuple(config)
        await saver.adelete_thread(config["configurable"]["thread_id"])
        assert await saver.aget_tuple(config) is None
        assert saver.conn.get_stats()["pool_available"] == 1
