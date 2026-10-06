"""Stable public Runs: real HTTP, PostgreSQL, claims and checkpoint restarts.

Uses the guarded disposable identity_test DB. No external LLM/Executor/Redis.
"""

from dtest.agent_service.runtime.initial_request import record_initial_request
from dtest.agent_service.runtime.user_resume import (
    record_user_resume,
    user_interrupt,
)

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID, uuid4
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, func, update
from sqlalchemy.engine import make_url
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command

import dtest.settings.loader as service_settings
import dtest.worker_service.command_worker as worker
import dtest.application.runs.execution as runs
import dtest.application.runs.runtime as graphs
import dtest.application.runs.projection as completion
import dtest.api_service.http.v1.routes.runs as routes
from dtest.agent_service.runtime.langgraph.checkpointer import (
    create_checkpointer,
)
from dtest.infrastructure.database.models.agent_run_model import (
    AgentRunModel as Run,
)
from dtest.infrastructure.database.models.agent_run_log_model import (
    AgentRunLogModel as Log,
)
from dtest.infrastructure.database.models.task_model import TaskModel as Task
from dtest.application.runs.task_events import TaskEventService
from tests.api_service.ownership_harness import (
    run_test_event as run_event_owned,
)
from dtest.contracts.events import EventContext, ExecutorEvent
from dtest.contracts.enums import TaskStatus
from tests.api_service.test_user_identity_postgres import (
    database_url,
    harness,
    headers,
    add_session,
    add_user,
)
from tests.api_service.test_run_cleanup_postgres import runtime, enqueue, rows
from tests.api_service.test_short_transactions_postgres import small_pool


def path(h, rid=None):
    return f"/api/v1/sessions/{h.session_id}/runs" + (f"/{rid}" if rid else "")


async def state(h, rid):
    response = await h.client.get(
        path(h, rid), headers=headers(h.user["user_id"])
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["run_id"] and "id" not in body
    return body


async def resume(h, current, key=None, command=None):
    return await h.client.post(
        path(h),
        headers={
            **headers(h.user["user_id"]),
            "Idempotency-Key": key or str(uuid4()),
        },
        json={
            "run_id": current["run_id"],
            "resume_token": current["resume_token"],
            "command": {
                "resume": {
                    "action": "approve_plan",
                    "plan_id": "test-plan",
                    "plan_revision": 1,
                    "input_values": {"legacy": command or {"approved": True}},
                }
            },
        },
    )


async def execute():
    item = await worker.claim_one()
    assert item is not None
    await worker.execute_claimed(item)


def waiting(kind="USER_APPROVAL", route="analysis"):
    return {
        "routing_result": {"route": route},
        "__interrupt__": [SimpleNamespace(value={"kind": kind})],
    }


@pytest.mark.asyncio
async def test_multiple_resumes_keep_id_and_list_one_resource(
    runtime, monkeypatch
):
    h = runtime
    monkeypatch.setattr(
        runs, "ainvoke_user_turn", AsyncMock(return_value=waiting())
    )
    monkeypatch.setattr(
        runs, "ainvoke_resume", AsyncMock(return_value=waiting())
    )
    first = await enqueue(h)
    assert "run_id" in first and "id" not in first
    await execute()
    old_token = None
    for i in range(3):
        current = await state(h, first["run_id"])
        assert (
            current["status"] == "waiting_input"
            and current["completed_at"] is None
        )
        assert current["resume_token"] != old_token
        old_token = current["resume_token"]
        response = await resume(h, current, key=f"resume-{i}")
        assert response.status_code == 202, response.text
        assert (
            response.json()["run_id"] == first["run_id"]
            and response.json()["status"] == "pending"
        )
        assert response.headers["location"] == path(h, first["run_id"])
        await execute()
    listed = (
        await h.client.get(path(h), headers=headers(h.user["user_id"]))
    ).json()["items"]
    assert len(listed) == 1 and listed[0]["run_id"] == first["run_id"]
    assert all("id" not in item for item in listed)
    async with h.factory() as db:
        invocations = list(
            await db.scalars(select(Run).order_by(Run.created_at))
        )
        assert (
            len(invocations) == 4 and len({r.run_id for r in invocations}) == 4
        )
        assert {r.public_run_id for r in invocations} == {
            UUID(first["run_id"])
        }
        assert {r.checkpoint_run_id for r in invocations} == {
            UUID(first["run_id"])
        }
    # Old private IDs resolve to current public state, never a stale root interrupt.
    alias = await state(h, invocations[-1].run_id)
    assert alias["run_id"] == first["run_id"] and alias["resume_token"] == str(
        invocations[-1].run_id
    )
    assert (
        await h.client.get(
            path(h, first["run_id"]) + "/join",
            headers=headers(h.user["user_id"]),
        )
    ).status_code == 404


@pytest.mark.asyncio
async def test_concurrent_replay_stale_tokens_and_payload_mismatch(
    runtime, monkeypatch
):
    h = runtime
    monkeypatch.setattr(
        runs, "ainvoke_user_turn", AsyncMock(return_value=waiting())
    )
    mocked = AsyncMock(return_value=waiting())
    monkeypatch.setattr(runs, "ainvoke_resume", mocked)
    first = await enqueue(h)
    await execute()
    current = await state(h, first["run_id"])
    duplicates = await asyncio.gather(
        *(resume(h, current, key="one-command") for _ in range(8))
    )
    assert all(
        r.status_code == 202 and r.json()["run_id"] == first["run_id"]
        for r in duplicates
    )
    async with h.factory() as db:
        assert await db.scalar(select(func.count()).select_from(Run)) == 2
    assert (
        await resume(
            h, current, key="one-command", command={"approved": False}
        )
    ).status_code == 409
    await execute()
    assert mocked.await_count == 1
    assert (await resume(h, current, key="one-command")).status_code == 202
    assert (await resume(h, current, key="stale-new-key")).status_code == 409
    current = await state(h, first["run_id"])
    racers = await asyncio.gather(*(resume(h, current) for _ in range(8)))
    assert sorted(r.status_code for r in racers) == [202] + [409] * 7


@pytest.mark.asyncio
async def test_initial_key_collision_and_invalid_public_commands(runtime):
    h = runtime
    hdr = {**headers(h.user["user_id"]), "Idempotency-Key": "start"}
    body = {"input": {"content": [{"type": "text", "text": "first"}]}}
    first = await h.client.post(path(h), headers=hdr, json=body)
    assert first.status_code == 202
    assert (await h.client.post(path(h), headers=hdr, json=body)).json()[
        "run_id"
    ] == first.json()["run_id"]
    body["input"]["content"][0]["text"] = "different"
    assert (
        await h.client.post(path(h), headers=hdr, json=body)
    ).status_code == 409
    assert (
        await h.client.post(path(h), headers=hdr, json={"command": {"x": 1}})
    ).status_code == 422
    for metadata in (
        {"checkpoint_run_id": str(uuid4())},
        {"_public_resume": str(uuid4())},
    ):
        assert (
            await h.client.post(
                path(h), headers=hdr, json={**body, "metadata": metadata}
            )
        ).status_code == 422


@pytest.mark.asyncio
async def test_owner_and_cross_run_tokens_are_rejected(runtime, monkeypatch):
    h = runtime
    monkeypatch.setattr(
        runs, "ainvoke_user_turn", AsyncMock(return_value=waiting())
    )
    first = await enqueue(h)
    second_session = await add_session(h, h.user)
    second = await enqueue(h, second_session)
    await execute()
    await execute()
    current = await state(h, first["run_id"])
    current["resume_token"] = second["run_id"]
    assert (await resume(h, current)).status_code == 409
    for suffix in ("", "/logs", "/stream"):
        assert (
            await h.client.get(
                path(h, first["run_id"]) + suffix, headers=headers("admin")
            )
        ).status_code == 404
    for suffix, body in [("/cancel", {})]:
        assert (
            await h.client.post(
                path(h, first["run_id"]) + suffix,
                headers={**headers("admin"), "Idempotency-Key": "x"},
                json=body,
            )
        ).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage", ["waiting_input", "waiting_executor", "recovery_required"]
)
async def test_cancel_and_session_lock_by_public_state(
    runtime, monkeypatch, stage
):
    h = runtime
    monkeypatch.setattr(
        runs,
        "ainvoke_user_turn",
        AsyncMock(
            return_value=waiting(
                "EXECUTOR_EVENT"
                if stage == "waiting_executor"
                else "USER_APPROVAL"
            )
        ),
    )
    first = await enqueue(h)
    await execute()
    if stage == "recovery_required":
        async with h.factory() as db:
            await db.execute(
                update(Task)
                .where(Task.task_id == UUID(first["task_id"]))
                .values(recovery_required=True)
            )
            await db.commit()
    before = await state(h, first["run_id"])
    assert before["status"] == stage
    response = await h.client.post(
        path(h, first["run_id"]) + "/cancel",
        headers=headers(h.user["user_id"]),
        json={"reason": "stop"},
    )
    assert response.status_code == (
        202 if stage == "waiting_input" else 409
    ), response.text
    after = await state(h, first["run_id"])
    if stage == "waiting_input":
        assert (
            after["status"] == "canceled"
            and after["completed_at"]
            and after["resume_token"] is None
        )
        assert after["cancel_reason"] == "stop"
        assert (
            await h.client.post(
                path(h, first["run_id"]) + "/cancel",
                headers=headers(h.user["user_id"]),
                json={},
            )
        ).status_code == 202
        await enqueue(h)
    else:
        assert after["status"] == stage and after["completed_at"] is None
        body = {"input": {"content": [{"type": "text", "text": "blocked"}]}}
        assert (
            await h.client.post(
                path(h),
                headers={
                    **headers(h.user["user_id"]),
                    "Idempotency-Key": "blocked",
                },
                json=body,
            )
        ).status_code == 409
        # Removed Task commands cannot bypass the Run external-wait guard.
        assert (
            await h.client.post(
                f"/api/v1/tasks/{first['task_id']}/cancel",
                headers=headers(h.user["user_id"]),
                json={},
            )
        ).status_code == 404


@pytest.mark.asyncio
async def test_sse_replays_all_invocations_and_logs_use_canonical_id(
    runtime, monkeypatch
):
    h = runtime
    monkeypatch.setattr(routes, "get_session_factory", lambda: h.factory)
    monkeypatch.setattr(
        runs, "ainvoke_user_turn", AsyncMock(return_value=waiting())
    )
    monkeypatch.setattr(
        runs,
        "ainvoke_resume",
        AsyncMock(
            return_value={
                "routing_result": {"route": "analysis"},
                "final_response": "done",
            }
        ),
    )
    first = await enqueue(h)
    await execute()
    current = await state(h, first["run_id"])
    response = await resume(h, current)
    assert response.status_code == 202
    await execute()
    async with h.factory() as db:
        invocations = list(
            await db.scalars(select(Run).order_by(Run.created_at))
        )
        for r in invocations:
            db.add(
                Log(
                    run_id=r.run_id,
                    event_key="same-per-invocation",
                    node="n",
                    event="result",
                    kind="agent",
                    payload={},
                )
            )
        await db.commit()
        events = await TaskEventService.list_after_public_run(
            db, run_id=UUID(first["run_id"]), sequence=0, limit=100
        )
    assert len({e.sequence for e in events}) == len(events)
    assert {e.run_id for e in events} == {r.run_id for r in invocations}
    cursor = events[2].sequence
    streamed = await h.client.get(
        path(h, first["run_id"]) + "/stream",
        headers={**headers(h.user["user_id"]), "Last-Event-ID": str(cursor)},
    )
    assert streamed.status_code == 200
    ids = [
        int(line[4:])
        for line in streamed.text.splitlines()
        if line.startswith("id: ")
    ]
    assert ids == [e.sequence for e in events if e.sequence > cursor]
    assert (
        "event: run.snapshot" in streamed.text
        and '"status":"success"' in streamed.text
    )
    assert str(invocations[-1].run_id) not in streamed.text
    logs = (
        await h.client.get(
            path(h, first["run_id"]) + "/logs",
            headers=headers(h.user["user_id"]),
        )
    ).json()
    assert len(logs["items"]) == 2 and {
        l["run_id"] for l in logs["items"]
    } == {first["run_id"]}
    assert logs["page"] == {"has_next": False, "next_cursor": None}
    result = await state(h, first["run_id"])
    assert (
        result["result"]["final_response"] == "done" and result["completed_at"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["faq", "analysis"])
async def test_simple_completion_keeps_history_and_new_run_has_new_id(
    runtime, monkeypatch, route
):
    h = runtime
    monkeypatch.setattr(
        runs,
        "ainvoke_user_turn",
        AsyncMock(return_value={"routing_result": {"route": route}}),
    )
    first = await enqueue(h)
    await execute()
    assert (await state(h, first["run_id"]))["status"] == "success"
    second = await enqueue(h)
    assert second["run_id"] != first["run_id"]
    async with h.factory() as db:
        task = await db.get(Task, UUID(first["task_id"]))
        assert task.status == TaskStatus.SUCCESS
    listed = (
        await h.client.get(
            path(h) + "?limit=1", headers=headers(h.user["user_id"])
        )
    ).json()
    assert len(listed["items"]) == 1 and listed["page"]["has_next"]
    next_page = (
        await h.client.get(
            path(h) + "?limit=1&cursor=" + listed["page"]["next_cursor"],
            headers=headers(h.user["user_id"]),
        )
    ).json()
    assert next_page["items"][0]["run_id"] == first["run_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["SUCCEEDED", "FAILED", "CANCELED"])
async def test_real_checkpoint_two_hitl_restart_and_executor_projection(
    runtime, monkeypatch, outcome
):
    h = runtime
    graph_task, execution, command_id, event_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )

    @record_initial_request
    async def start(s):
        return {
            **s,
            "routing_result": {"route": "analysis"},
            "task_id": str(graph_task),
            "execution_id": str(execution),
        }

    @record_user_resume
    async def approval1(s):
        return {
            **s,
            "answer1": user_interrupt({"kind": "USER_APPROVAL", "stage": 1}),
        }

    @record_user_resume
    async def approval2(s):
        return {
            **s,
            "answer2": user_interrupt({"kind": "USER_APPROVAL", "stage": 2}),
        }

    async def external(s):
        interrupt({"kind": "EXECUTOR_EVENT"})
        return {
            **s,
            "execution_status": outcome,
            "ew_receipts": {str(command_id): str(event_id)},
            "final_response": {"outcome": outcome},
        }

    def build(saver):
        builder = StateGraph(dict)
        for name, node in [
            ("start", start),
            ("a", approval1),
            ("b", approval2),
            ("external", external),
        ]:
            builder.add_node(name, node)
        for a, b in [
            (START, "start"),
            ("start", "a"),
            ("a", "b"),
            ("b", "external"),
            ("external", END),
        ]:
            builder.add_edge(a, b)
        return builder.compile(checkpointer=saver)

    dsn = (
        make_url(service_settings.get_settings().database.database_url)
        .set(drivername="postgresql")
        .render_as_string(hide_password=False)
    )

    @asynccontextmanager
    async def graph_context():
        async with create_checkpointer(
            database_url=dsn,
            setup_on_start=True,
            min_size=1,
            max_size=2,
            timeout=2,
        ) as saver:
            yield build(saver)

    first = await enqueue(h)
    rt = graphs.AgentGraphRuntime()
    monkeypatch.setattr(rt, "_graph_context", graph_context)
    monkeypatch.setattr(graphs, "runtime", rt)
    try:
        await execute()
        for _ in range(2):
            current = await state(h, first["run_id"])
            assert current["status"] == "waiting_input"
            assert (await resume(h, current)).json()["run_id"] == first[
                "run_id"
            ]
            await execute()
        current = await state(h, first["run_id"])
        assert (
            current["status"] == "waiting_executor"
            and current["resume_token"] is None
        )
        assert await worker.claim_one() is None
        await rt.shutdown()
        # A new runtime/pool rehydrates solely from persistent checkpoints.
        rt = graphs.AgentGraphRuntime()
        monkeypatch.setattr(rt, "_graph_context", graph_context)
        monkeypatch.setattr(graphs, "runtime", rt)
        monkeypatch.setattr(
            completion, "get_session_factory", lambda: h.factory
        )
        context = EventContext(
            "test",
            h.session_id,
            str(graph_task),
            execution,
            command_id,
            ExecutorEvent(
                event_id=event_id,
                execution_id=execution,
                event_type="execution.completed",
                event_sequence=1,
                schema_version="1.0",
                occurred_at="2026-09-29T00:00:00+00:00",
                payload={},
            ),
        )
        async with rt.open_graph() as graph:
            snapshot = await graph.aget_state(context.graph_config)
            assert (
                snapshot.next
                and snapshot.values["answer2"]["resume"]["action"]
                == "approve_plan"
            )
            assert snapshot.values["answer2"]["resume"]["input_values"][
                "legacy"
            ] == {"approved": True}

            async def handle():
                await graph.ainvoke(
                    Command(resume={"completed": True}),
                    context.graph_config,
                    durability="sync",
                )
                await completion.synchronize_executor_completion(
                    context, graph
                )

            await run_event_owned(context, handle)
            _, task = await rows(h, first["run_id"])
            sequence = task.last_event_sequence
            await completion.synchronize_executor_completion(context, graph)
        _, task = await rows(h, first["run_id"])
        assert task.last_event_sequence == sequence
        finished = await state(h, first["run_id"])
        assert (
            finished["run_id"] == first["run_id"]
            and finished["status"]
            == {
                "SUCCEEDED": "success",
                "FAILED": "error",
                "CANCELED": "canceled",
            }[outcome]
        )
        assert finished["result"]["final_response"] == {"outcome": outcome}
        await enqueue(h)
    finally:
        await rt.shutdown()


@pytest.mark.asyncio
async def test_resume_cancel_race_never_accepts_a_second_live_invocation(
    runtime, monkeypatch
):
    h = runtime
    monkeypatch.setattr(
        runs, "ainvoke_user_turn", AsyncMock(return_value=waiting())
    )
    monkeypatch.setattr(
        runs,
        "ainvoke_resume",
        AsyncMock(return_value={"routing_result": {"route": "analysis"}}),
    )
    first = await enqueue(h)
    await execute()
    current = await state(h, first["run_id"])
    res, cancel = await asyncio.gather(
        resume(h, current),
        h.client.post(
            path(h, first["run_id"]) + "/cancel",
            headers=headers(h.user["user_id"]),
            json={},
        ),
    )
    assert cancel.status_code == 202
    assert res.status_code in (202, 409)
    claimed = await worker.claim_one()
    if claimed:
        await worker.execute_claimed(claimed)
    assert (await state(h, first["run_id"]))["status"] == "canceled"
    assert await worker.claim_one() is None
    await enqueue(h)


@pytest.mark.asyncio
async def test_same_sse_survives_hitl_and_releases_single_db_connection(
    small_pool, monkeypatch
):
    import json

    h = small_pool
    monkeypatch.setattr(routes, "get_session_factory", lambda: h.factory)
    monkeypatch.setattr(
        runs, "ainvoke_user_turn", AsyncMock(return_value=waiting())
    )
    monkeypatch.setattr(
        runs,
        "ainvoke_resume",
        AsyncMock(return_value={"routing_result": {"route": "analysis"}}),
    )
    first = await enqueue(h)
    await execute()
    async with h.factory() as db:
        from dtest.infrastructure.database.models.session_model import (
            SessionModel,
        )

        user_id = await db.scalar(
            select(SessionModel.user_id).where(
                SessionModel.session_id == UUID(h.session_id)
            )
        )
        response = await routes.stream_run(
            SimpleNamespace(is_disconnected=AsyncMock(return_value=False)),
            UUID(h.session_id),
            UUID(first["run_id"]),
            last_event_id=None,
            user_id=user_id,
            db=db,
        )
    stream = response.body_iterator
    try:
        async with asyncio.timeout(5):
            async for chunk in stream:
                assert h.engine.pool.checkedout() == 0
                if chunk.startswith("event: run.snapshot"):
                    paused = json.loads(chunk.split("data: ", 1)[1])["data"]
                    assert paused["status"] == "waiting_input"
                    break
            # Hold this same HTTP stream open while another request resumes.
            assert (await resume(h, paused)).status_code == 202
            await execute()
            finished = False
            async for chunk in stream:
                assert h.engine.pool.checkedout() == 0
                if chunk.startswith("event: run.snapshot"):
                    finished = (
                        json.loads(chunk.split("data: ", 1)[1])["data"][
                            "status"
                        ]
                        == "success"
                    )
            assert finished
    finally:
        await stream.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("old_status", ["interrupted", "running"])
async def test_taskless_legacy_cancel_finishes_only_confirmed_paused_invocation(
    runtime, old_status
):
    from dtest.contracts.enums import AgentRunStatus

    h = runtime
    async with h.factory() as db:
        run = Run(
            session_id=UUID(h.session_id),
            status=AgentRunStatus(old_status),
            idempotency_key="old-faq",
        )
        db.add(run)
        await db.commit()
        rid = run.run_id
    response = await h.client.post(
        path(h, rid) + "/cancel", headers=headers(h.user["user_id"]), json={}
    )
    assert response.status_code == (
        202 if old_status == "interrupted" else 409
    )
    final = await state(h, rid)
    assert final["status"] == (
        "canceled" if old_status == "interrupted" else "running"
    )
    assert bool(final["completed_at"]) == (old_status == "interrupted")


@pytest.mark.asyncio
async def test_snapshot_reuse_keeps_fresh_rows_and_preserves_dirty_orm_objects(
    runtime,
):
    from dtest.application.runs.service import PublicRunService

    h = runtime
    first = await enqueue(h)
    second_session = await add_session(h, h.user)
    second = await enqueue(h, second_session)
    first_id, second_id = UUID(first["run_id"]), UUID(second["run_id"])
    async with h.factory() as db:
        run = await db.get(Run, first_id)
        task = await db.get(Task, UUID(first["task_id"]))
        local_failure = {"local": "not flushed"}
        run.failure = local_failure
        # Reusing an expanding bind must not retain IDs or a previous list size.
        for ids, expected in [
            ([first_id], {first_id}),
            ([first_id, second_id], {first_id, second_id}),
            ([], set()),
            ([second_id, second_id, uuid4()], {second_id}),
            ([first_id], {first_id}),
        ]:
            snapshots = await PublicRunService.snapshots(db, ids)
            assert set(snapshots) == expected
        before = (await PublicRunService.snapshots(db, [first_id]))[first_id]
        assert before[1].failure is None and not before[2].recovery_required
        async with h.factory() as writer:
            await writer.execute(
                update(Task)
                .where(Task.task_id == task.task_id)
                .values(recovery_required=True)
            )
            await writer.commit()
        after = (await PublicRunService.snapshots(db, [first_id]))[first_id]
        assert after[
            2
        ].recovery_required  # Fresh DB row, even in the same reader session.
        assert after[1].failure is None
        assert run.failure == local_failure and run in db.dirty
        assert (
            not task.recovery_required
        )  # Scalar projection did not refresh ORM objects.


@pytest.mark.asyncio
async def test_reused_public_read_isolates_concurrent_users_and_session_ids(
    runtime,
):
    h = runtime
    first = await enqueue(h)
    second_session = await add_session(h, h.user)
    second = await enqueue(h, second_session)
    other_user = await add_user(h, name="another-user")
    other_session = await add_session(h, other_user)
    response = await h.client.post(
        f"/api/v1/sessions/{other_session}/runs",
        headers={
            **headers(other_user["user_id"]),
            "Idempotency-Key": str(uuid4()),
        },
        json={
            "input": {"content": [{"type": "text", "text": "another request"}]}
        },
    )
    assert response.status_code == 202, response.text
    third = response.json()
    targets = [
        (h.user, h.session_id, first),
        (h.user, second_session, second),
        (other_user, other_session, third),
    ]
    for _ in range(3):
        responses = await asyncio.gather(
            *(
                h.client.get(
                    f"/api/v1/sessions/{sid}/runs/{run['run_id']}",
                    headers=headers(user["user_id"]),
                )
                for user, sid, run in targets
            )
        )
        assert all(r.status_code == 200 for r in responses)
        assert [r.json()["run_id"] for r in responses] == [
            t[2]["run_id"] for t in targets
        ]
    for user, sid, rid in [
        (h.user, second_session, first["run_id"]),
        (h.user, h.session_id, third["run_id"]),
        (h.user, other_session, third["run_id"]),
        (other_user, h.session_id, first["run_id"]),
        (h.user, h.session_id, str(uuid4())),
    ]:
        rejected = await h.client.get(
            f"/api/v1/sessions/{sid}/runs/{rid}",
            headers=headers(user["user_id"]),
        )
        assert rejected.status_code == 404
    assert (await state(h, first["run_id"]))["run_id"] == first["run_id"]
