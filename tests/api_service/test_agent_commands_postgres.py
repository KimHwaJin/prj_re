"""Unified user/Event command scheduling on disposable PostgreSQL + local Redis."""

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from psycopg_pool import AsyncConnectionPool
from redis.asyncio import Redis
from sqlalchemy import select, text, update
from sqlalchemy.engine import make_url

import dtest.settings.loader as service_settings
import dtest.worker_service.command_worker as worker
import dtest.application.runs.execution as execution
from dtest.contracts.enums import AgentRunStatus, TaskStatus
from dtest.application.runs.lifecycle import execution_health
from dtest.infrastructure.database.models.agent_command_model import (
    AgentCommandModel as Command,
)
from dtest.infrastructure.database.models.session_execution_model import (
    SessionExecutionModel as Owner,
)
from dtest.infrastructure.database.models.task_model import TaskModel as Task
from dtest.application.runs.commands.types import ClaimedEvent
from dtest.contracts.values import utc_now
from dtest.worker_service.executor_events.consumer import (
    AckDecision,
    StreamMessage,
)
from dtest.worker_service.executor_events.ingress import Ingress
from dtest.worker_service.executor_events.runtime import ExecutorWorker
from dtest.infrastructure.database.event_store import Store
from dtest.contracts.events import DeferEvent, ExecutorEvent
from dtest.contracts.execution import ExecutionNeedsRecovery
from tests.api_service.test_user_identity_postgres import (
    database_url,
    harness,
    add_session,
)
from tests.api_service.test_run_cleanup_postgres import runtime, enqueue

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.asyncio


async def backfill(factory):
    """Exercise the one-time Alembic transition without a production CLI."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "retirement",
        ROOT / "migrations/versions/0003_retire_unused_worker_storage.py",
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    async with factory() as db:
        count = await db.run_sync(
            lambda sync: migration._admit_legacy_work(
                sync.connection(),
                service_settings.get_settings().worker.namespace,
            )
        )
        await db.commit()
        return count


async def seed_legacy_event_mirror(h):
    """Old-version fixtures only; the application never writes this table."""
    async with h.factory() as db:
        await db.execute(
            text("""INSERT INTO ew_commands(namespace,command_id,event_id,execution_id,sequence,created_by,updated_by)
            SELECT namespace,command_id,(payload->'event'->>'event_id')::uuid,
            (payload->>'execution_id')::uuid,(payload->'event'->>'event_sequence')::bigint,'legacy','legacy'
            FROM agent_commands WHERE kind='executor_resume' ON CONFLICT DO NOTHING""")
        )
        await db.commit()


@pytest_asyncio.fixture
async def commands(runtime, tmp_path):
    h = runtime
    url = h.engine.url.render_as_string(hide_password=False)
    config = tmp_path / "events.yml"
    config.write_text(
        "database_url: "
        + url
        + "\nAGENT_WORKER_ENABLED: false\nEVENT_WORKER_ENABLED: false\n"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "alembic.ini",
            "upgrade",
            "ew_0001",
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "SERVICE_CONFIG_FILE": str(config),
            "PYTHONPATH": str(ROOT / "src"),
        },
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    async with h.engine.begin() as db:
        await db.execute(
            text(
                "TRUNCATE ew_inbox,ew_bindings,ew_commands,session_e"
                "xecutions "
                "CASCADE"
            )
        )
    pool = AsyncConnectionPool(
        make_url(url)
        .set(drivername="postgresql")
        .render_as_string(hide_password=False),
        open=False,
        min_size=1,
        max_size=2,
    )
    await pool.open(wait=True)
    h.store = Store(pool, service_settings.get_settings().worker.namespace)
    try:
        yield h
    finally:
        await pool.close()


def event(execution_id, sequence=1, kind="execution.completed"):
    return ExecutorEvent(
        event_id=uuid4(),
        execution_id=execution_id,
        event_type=kind,
        event_sequence=sequence,
        schema_version="1.0",
        occurred_at="2026-10-03T00:00:00Z",
        payload={},
    )


async def admit_event(h, session_id=None, *, sequence=1):
    eid = uuid4()
    await h.store.register(
        execution_id=eid,
        session_id=session_id or h.session_id,
        task_id=str(uuid4()),
    )
    payload = event(eid, sequence)
    await h.store.ingest(payload)
    assert await h.store.advance(eid, {"execution.completed"}, 10) == (1, None)
    return payload


async def rows(h):
    async with h.factory() as db:
        return list(
            await db.scalars(select(Command).order_by(Command.ordinal))
        )


async def until(predicate):
    async with asyncio.timeout(4):
        while not await predicate():
            await asyncio.sleep(0.01)


async def test_api_admission_and_command_commit_together_and_replay_once(
    commands,
):
    h = commands
    public = await enqueue(h)
    pending = await rows(h)
    assert (
        len(pending) == 1 and str(pending[0].invocation_id) == public["run_id"]
    )
    assert pending[0].state == "READY" and pending[0].kind == "user_start"
    async with h.factory() as db:
        from dtest.infrastructure.database.models.agent_run_model import (
            AgentRunModel,
        )

        invocation = await db.get(AgentRunModel, pending[0].invocation_id)
        from dtest.application.runs.admission import enqueue as admit
        from dtest.contracts.resources.run_schema import RunCreate

        replay = await admit(
            db,
            UUID(invocation.metadata_json["requested_by_user_id"]),
            UUID(h.session_id),
            RunCreate(input=invocation.input_json),
            invocation.idempotency_key,
        )
        assert replay.run_id == invocation.run_id
    assert len(await rows(h)) == 1


async def test_event_route_is_atomic_and_duplicates_do_not_create_commands(
    commands,
):
    h = commands
    eid = uuid4()
    await h.store.register(
        execution_id=eid, session_id=h.session_id, task_id=str(uuid4())
    )
    incoming = event(eid)
    await h.store.ingest(incoming)
    original_id = h.session_id
    # Invalid binding must roll back both source sequence and command admission.
    async with h.store.pool.connection() as conn:
        await conn.execute(
            "UPDATE ew_bindings SET session_id=%s WHERE execution_id=%s",
            (str(uuid4()), eid),
        )
    with pytest.raises(Exception):
        await h.store.advance(eid, {"execution.completed"}, 10)
    async with h.store.pool.connection() as conn:
        assert (
            await (
                await conn.execute(
                    (
                        "SELECT last_sequence FROM ew_bindings WHERE "
                        "execution_id=%s"
                    ),
                    (eid,),
                )
            ).fetchone()
        )[0] == 0
        assert (
            await (
                await conn.execute("SELECT count(*) FROM agent_commands")
            ).fetchone()
        )[0] == 0
        await conn.execute(
            "UPDATE ew_bindings SET session_id=%s WHERE execution_id=%s",
            (original_id, eid),
        )
    assert await h.store.advance(eid, {"execution.completed"}, 10) == (1, None)
    await h.store.ingest(incoming)
    assert await h.store.advance(eid, {"execution.completed"}, 10) == (0, None)
    assert len(await rows(h)) == 1


async def test_sequence_gap_waits_without_losing_event(commands):
    h = commands
    eid = uuid4()
    await h.store.register(
        execution_id=eid, session_id=h.session_id, task_id=str(uuid4())
    )
    later = event(eid, 2)
    first = event(eid, 1)
    await h.store.ingest(later)
    assert await h.store.advance(eid, {"execution.completed"}, 10) == (0, 0)
    assert not await rows(h)
    await h.store.ingest(first)
    assert await h.store.advance(eid, {"execution.completed"}, 10) == (2, None)
    assert [c.payload["event"]["event_sequence"] for c in await rows(h)] == [
        1,
        2,
    ]


async def test_two_replicas_claim_one_command_only_and_no_expiry_takeover(
    commands,
):
    h = commands
    await admit_event(h)
    results = await asyncio.gather(*(worker.claim_one() for _ in range(8)))
    claimed = [item for item in results if item is not None]
    assert len(claimed) == 1 and isinstance(claimed[0], ClaimedEvent)
    async with h.factory() as db:
        await db.execute(
            update(Owner).values(heartbeat_at=utc_now() - timedelta(days=7))
        )
        await db.commit()
    assert await worker.claim_one() is None
    assert (await rows(h))[0].state == "RUNNING"


async def test_earlier_retry_blocks_same_session_but_other_session_advances(
    commands, monkeypatch
):
    h = commands
    await admit_event(h)
    await admit_event(h)
    first = await worker.claim_one()
    monkeypatch.setattr(
        worker,
        "execute_event",
        AsyncMock(side_effect=DeferEvent("checkpoint not ready")),
    )
    await worker.execute_claimed(first)
    pending = await rows(h)
    assert pending[0].state == "READY" and pending[0].failure_attempts == 0
    async with h.factory() as db:
        await db.execute(
            update(Command)
            .where(Command.command_id == first.command_id)
            .values(available_at=utc_now() + timedelta(hours=1))
        )
        await db.commit()
    other = await add_session(h, h.user)
    await admit_event(h, other)
    picked = await worker.claim_one()
    assert str(picked.session_id) == other
    assert await worker.claim_one() is None


async def test_user_and_event_share_total_capacity_and_reuse_idle_slots(
    commands, monkeypatch
):
    from dataclasses import replace

    h = commands
    configured = service_settings.get_settings()
    monkeypatch.setattr(
        service_settings,
        "_snapshot",
        replace(
            configured,
            commands=configured.commands.model_copy(
                update={"agent_worker_concurrency": 2}
            ),
        ),
    )
    starts = []
    active = 0
    peak = 0
    release = asyncio.Event()

    async def pause(label):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        starts.append(label)
        try:
            await release.wait()
        finally:
            active -= 1

    async def graph(**_):
        await pause("user")
        return {"routing_result": {"route": "analysis"}}

    async def result(_):
        await pause("event")

    monkeypatch.setattr(execution, "ainvoke_user_turn", graph)
    monkeypatch.setattr(worker, "execute_event", result)
    await enqueue(h)
    second = await add_session(h, h.user)
    await admit_event(h, second)
    third = await add_session(h, h.user)
    await admit_event(h, third)
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run_forever(stop_event=stop))

    async def filled():
        return len(starts) == 2

    try:
        await until(filled)
        assert sorted(starts) == ["event", "user"] and peak == 2
        assert [row.state for row in await rows(h)].count("READY") == 1
        release.set()

        async def finished():
            return all(row.state == "DONE" for row in await rows(h))

        await until(finished)
        assert starts.count("event") == 2 and peak == 2
    finally:
        stop.set()
        release.set()
        await asyncio.wait_for(task, 4)


async def test_crash_after_claim_is_not_replayed_after_process_restart(
    commands, monkeypatch
):
    h = commands
    await admit_event(h)
    first = await worker.claim_one()
    with pytest.raises(RuntimeError, match="ownership"):
        await backfill(h.factory)
    assert await worker.claim_one() is None
    assert not execution_health.faults
    assert (await rows(h))[0].owner_token == first.owner.token


async def test_legacy_pending_backfill_is_idempotent_and_preserves_run_id(
    commands,
):
    h = commands
    original = await enqueue(h)
    async with h.factory() as db:
        await db.execute(text("DELETE FROM agent_commands"))
        await db.commit()
    assert await backfill(h.factory) == 1
    assert await backfill(h.factory) == 0
    assert str((await rows(h))[0].command_id) == original["run_id"]


async def test_ingress_acks_after_inbox_commit_before_graph_execution(
    commands,
):
    h = commands
    eid = uuid4()
    await h.store.register(
        execution_id=eid, session_id=h.session_id, task_id=str(uuid4())
    )
    incoming = event(eid)
    fields = incoming.model_dump(mode="json")
    import json

    fields = {
        key: str(value) for key, value in fields.items() if key != "payload"
    }
    fields["payload"] = "{}"
    response = await Ingress(h.store).handle(StreamMessage("1-0", fields))
    assert response.decision == AckDecision.ACK
    assert not await rows(h)
    await h.store.advance(eid, {"execution.completed"}, 10)
    assert (await rows(h))[0].state == "READY"


async def test_real_redis_to_persistent_graph_and_public_completion(
    commands, monkeypatch
):
    """Real Streams/Inbox/ledger/claim/checkpoint/projection, fixture Executor result."""
    from langgraph.graph import StateGraph, START, END
    from dtest.agent_service.runtime.initial_request import (
        record_initial_request,
    )
    from dtest.agent_service.runtime.executor_boundary import (
        ExecutorBoundaryNodes,
    )
    from dtest.agent_service.runtime.langgraph.checkpointer import (
        create_checkpointer,
    )
    from dtest.application.runs import runtime as graphs
    from dtest.application.runs import projection
    from tests.api_service.test_public_run_postgres import state

    h = commands
    execution_id = uuid4()
    graph_task = uuid4()
    boundary = ExecutorBoundaryNodes(h.store)

    @record_initial_request
    async def start(value):
        return {
            **value,
            "routing_result": {"route": "analysis"},
            "task_id": str(graph_task),
            "execution_id": str(execution_id),
        }

    async def register(value, config):
        return {**value, **await boundary.register_execution(value, config)}

    async def wait(value):
        return {**value, **boundary.wait_executor_event(value)}

    async def finish(value):
        return {
            **value,
            **boundary.record_executor_receipt(value),
            "execution_status": "SUCCEEDED",
            "final_response": {"text": "finished"},
        }

    @asynccontextmanager
    async def graph_context():
        async with create_checkpointer(
            database_url=h.store.pool.conninfo,
            setup_on_start=True,
            min_size=1,
            max_size=2,
            timeout=2,
        ) as saver:
            graph = StateGraph(dict)
            for name, node in [
                ("start", start),
                ("register", register),
                ("wait", wait),
                ("finish", finish),
            ]:
                graph.add_node(name, node)
            for left, right in [
                (START, "start"),
                ("start", "register"),
                ("register", "wait"),
                ("wait", "finish"),
                ("finish", END),
            ]:
                graph.add_edge(left, right)
            yield graph.compile(checkpointer=saver)

    rt = graphs.AgentGraphRuntime()
    monkeypatch.setattr(rt, "_graph_context", graph_context)
    monkeypatch.setattr(graphs, "runtime", rt)
    monkeypatch.setattr(projection, "get_session_factory", lambda: h.factory)
    redis_url = os.environ.get("DTEST_COMMAND_TEST_REDIS_URL")
    if not redis_url:
        pytest.skip(
            "DTEST_COMMAND_TEST_REDIS_URL must target disposable Redis"
        )
    stream = f"unified-test:{uuid4()}:events"
    group = f"unified-test:{uuid4()}"
    settings = service_settings.get_settings().worker.model_copy(
        update={
            "database_url": h.store.pool.conninfo,
            "redis_url": redis_url,
            "executor_event_stream": stream,
            "event_group_name": group,
            "pool_size": 2,
            "ingress_concurrency": 1,
            "poll_seconds": 0.02,
            "idle_poll_seconds": 0.05,
            "health_port": 0,
            "request_timeout_seconds": 1,
        }
    )
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    stop = asyncio.Event()
    loops = []
    try:
        public = await enqueue(h)
        await worker.execute_claimed(await worker.claim_one())
        assert (await state(h, public["run_id"]))[
            "status"
        ] == "waiting_executor"
        assert await worker.claim_one() is None
        assert (await rows(h))[0].state == "DONE"
        async with ExecutorWorker(
            settings, {"execution.completed"}
        ) as ingress:
            loops = [
                asyncio.create_task(ingress.run(stop_event=stop)),
                asyncio.create_task(worker.run_forever(stop_event=stop)),
            ]
            incoming = event(execution_id)
            fields = incoming.model_dump(mode="json")
            fields = {
                key: str(value)
                for key, value in fields.items()
                if key != "payload"
            }
            fields["payload"] = "{}"
            await redis.xadd(stream, fields)

            async def complete():
                return (await state(h, public["run_id"]))[
                    "status"
                ] == "success"

            await until(complete)

            async def all_done():
                return all(row.state == "DONE" for row in await rows(h))

            await until(all_done)
            before = await state(h, public["run_id"])
            await redis.xadd(stream, fields)

            async def consumed():
                groups = await redis.xinfo_groups(stream)
                return (
                    groups
                    and groups[0]["pending"] == 0
                    and groups[0].get("lag") == 0
                )

            await until(consumed)
            assert len(await rows(h)) == 2
            after = await state(h, public["run_id"])
            assert (
                after["run_id"] == public["run_id"]
                and after["status"] == "success"
            )
            assert after["result"] == before["result"]
            assert not await redis.exists(f"{settings.namespace}:commands")
            stop.set()
            await asyncio.wait_for(asyncio.gather(*loops), 4)
    finally:
        stop.set()
        await asyncio.gather(*loops, return_exceptions=True)
        await redis.delete(stream)
        await redis.aclose()
        await rt.shutdown()


async def test_permanent_rejection_does_not_block_later_session_commands(
    commands, monkeypatch
):
    from dtest.contracts.events import RejectEvent

    h = commands
    await admit_event(h)
    await admit_event(h)
    first = await worker.claim_one()
    monkeypatch.setattr(
        worker,
        "execute_event",
        AsyncMock(side_effect=RejectEvent("obsolete target")),
    )
    await worker.execute_claimed(first)
    assert (await rows(h))[0].state == "FAILED"
    second = await worker.claim_one()
    assert second is not None and second.command_id != first.command_id


async def test_event_cancellation_records_recovery_after_confirmed_graph_stop(
    commands, monkeypatch
):
    h = commands
    await admit_event(h)
    item = await worker.claim_one()
    entered = asyncio.Event()
    stopped = asyncio.Event()

    async def graph(_):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(worker, "execute_event", graph)
    running = asyncio.create_task(worker.execute_claimed(item))
    await entered.wait()
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert stopped.is_set()
    row = (await rows(h))[0]
    assert row.state == "RECOVERY" and row.owner_token == item.owner.token
    assert not execution_health.healthy
    assert await h.store.counts() == {"command:RECOVERY": 1, "inbox:ROUTED": 1}


async def test_outcome_rejects_changed_command_owner(commands):
    from dtest.application.runs.commands.outcome import record

    h = commands
    await admit_event(h)
    item = await worker.claim_one()
    async with h.factory() as db:
        await db.execute(update(Command).values(owner_token=uuid4()))
        await db.commit()
    with pytest.raises(ExecutionNeedsRecovery, match="owner changed"):
        await record(h.factory, item)
    assert (await rows(h))[0].state == "RUNNING"


async def test_legacy_events_backfill_preserves_identity_and_payload(commands):
    h = commands
    incoming = await admit_event(h)
    original = (await rows(h))[0]
    await seed_legacy_event_mirror(h)
    async with h.factory() as db:
        await db.execute(
            text(
                "UPDATE ew_commands SET failure_attempts=3,last_erro"
                "r='legacy failure'"
            )
        )
        await db.execute(text("DELETE FROM agent_commands"))
        await db.commit()
    assert await backfill(h.factory) == 1
    restored = (await rows(h))[0]
    assert restored.command_id == original.command_id
    assert restored.payload["event"]["event_id"] == str(incoming.event_id)
    assert restored.session_id == original.session_id
    assert (
        restored.failure_attempts == 3
        and restored.last_error == "legacy failure"
    )
    assert await backfill(h.factory) == 0


async def test_partial_migration_cannot_reorder_missing_old_session_command(
    commands,
):
    h = commands
    await admit_event(h)
    await admit_event(h)
    original = await rows(h)
    await seed_legacy_event_mirror(h)
    async with h.factory() as db:
        await db.execute(
            text("DELETE FROM agent_commands WHERE command_id=:id"),
            {"id": original[0].command_id},
        )
        await db.commit()
    with pytest.raises(RuntimeError, match="ordered migration"):
        await backfill(h.factory)
    assert len(await rows(h)) == 1


async def test_failed_command_admission_rolls_back_user_invocation_and_task(
    commands, monkeypatch
):
    from dtest.application.runs.commands import admission
    from dtest.infrastructure.database.models.agent_run_model import (
        AgentRunModel,
    )

    monkeypatch.setattr(
        admission,
        "enqueue_user",
        AsyncMock(side_effect=RuntimeError("ledger unavailable")),
    )
    import httpx

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=commands.app, raise_app_exceptions=False
        ),
        base_url="http://test",
    ) as client:
        response = await client.post(
            f"/api/v1/sessions/{commands.session_id}/runs",
            headers={
                "X-User-Id": commands.user["user_id"],
                "Idempotency-Key": str(uuid4()),
            },
            json={
                "input": {
                    "content": [{"type": "text", "text": "test request"}]
                }
            },
        )
    assert response.status_code == 500
    async with commands.factory() as db:
        assert await db.scalar(select(AgentRunModel.run_id)) is None
        assert await db.scalar(select(Task.task_id)) is None
        assert await db.scalar(select(Command.command_id)) is None


async def test_reused_claim_query_binds_namespace_and_fresh_deadline(
    commands, monkeypatch
):
    import dtest.application.runs.commands.claim as claim

    h = commands
    await admit_event(h)
    pending = (await rows(h))[0]
    future = utc_now() + timedelta(hours=1)
    async with h.factory() as db:
        await db.execute(
            update(Command)
            .where(Command.command_id == pending.command_id)
            .values(available_at=future)
        )
        await db.commit()
    # Reusing SQL must never reuse an earlier namespace or current-time value.
    assert await claim.claim_one(h.factory, h.store.namespace) is None
    monkeypatch.setattr(
        claim, "utc_now", lambda: future + timedelta(seconds=1)
    )
    assert await claim.claim_one(h.factory, "another-namespace") is None
    picked = await claim.claim_one(h.factory, h.store.namespace)
    assert picked.command_id == pending.command_id
    assert await claim.claim_one(h.factory, h.store.namespace) is None
