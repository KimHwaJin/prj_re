"""Fresh locked state, cancellation serialization and rollback on real PG."""
from types import SimpleNamespace
from uuid import UUID, uuid4
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text, update
from sqlalchemy.exc import DBAPIError

from dtest.worker_service import command_worker as worker
from dtest.application.runs import execution, projection
from dtest.application.runs.persistence import graph as graph_crud_persistence
from dtest.application.runs.task_events import TaskEventService
from dtest.application.runs.claim_context import bind_execution_claim
from dtest.contracts.enums import AgentRunStatus, TaskStatus
from dtest.infrastructure.database.models.agent_run_model import AgentRunModel
from dtest.infrastructure.database.models.task_model import TaskModel
from dtest.contracts.events import DeferEvent
from dtest.contracts.execution import ExecutionNeedsRecovery
from tests.api_service.test_user_identity_postgres import database_url, harness
from tests.api_service.test_run_cleanup_postgres import runtime, enqueue, rows
from tests.api_service.test_projection_roundtrips_postgres import uid

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("stale", [False, True])
async def test_prepare_reads_committed_fields_and_rejects_changed_owner(runtime, stale):
    h = runtime
    queued = await enqueue(h)
    item = await worker.claim_one()
    checkpoint = item.claim.run_id
    async with h.factory() as db:
        await db.execute(update(AgentRunModel).where(AgentRunModel.run_id == item.claim.run_id)
                         .values(metadata_json={"_model_selection": {"fresh": "committed"}}))
        fields = {"checkpoint_run_id": checkpoint}
        if stale:
            fields["lock_token"] = uuid4()
        await db.execute(update(TaskModel).where(TaskModel.task_id == item.claim.task_id).values(**fields))
        await db.commit()
    with bind_execution_claim(item.claim):
        if stale:
            with pytest.raises(ExecutionNeedsRecovery):
                await execution.prepare(item.claim, item.user_id, item.session_id, item.payload)
        else:
            result = await execution.prepare(item.claim, item.user_id, item.session_id, item.payload)
            assert result.model_selection == {"fresh": "committed"}
            assert result.checkpoint_id == checkpoint and result.run_id == item.claim.run_id
    run, task = await rows(h, queued["run_id"])
    assert run.status == AgentRunStatus.RUNNING and task.status == TaskStatus.RUNNING


async def waiting(h, monkeypatch):
    queued = await enqueue(h)
    graph_task, execution_id, command_id, event_id = (uuid4() for _ in range(4))
    async with h.factory() as db:
        await db.execute(update(TaskModel).where(TaskModel.task_id == UUID(queued["task_id"]))
                         .values(graph_task_id=graph_task, status=TaskStatus.WAITING_INPUT))
        await db.execute(update(AgentRunModel).where(AgentRunModel.run_id == UUID(queued["run_id"]))
                         .values(status=AgentRunStatus.INTERRUPTED))
        await db.commit()
    monkeypatch.setattr(projection, "get_session_factory", lambda: h.factory)
    # Graph receipt persistence has its own existing real-graph tests.
    monkeypatch.setattr(graph_crud_persistence, "persist_graph_state", AsyncMock())
    context = SimpleNamespace(session_id=h.session_id, task_id=str(graph_task), execution_id=execution_id,
                              command_id=command_id, event=SimpleNamespace(event_id=event_id))
    snapshot = SimpleNamespace(next=(), tasks=(), values={
        "agent_runtime": "agentic-planning-v1", "user_id": str(await uid(h)),
        "agent_run_id": queued["run_id"], "task_id": str(graph_task),
        "execution_id": str(execution_id), "ew_receipts": {str(command_id): str(event_id)},
        "final_response": {"text": "finished"},
    })
    return queued, context, snapshot


@pytest.mark.parametrize("guard", ["uncommitted_wait", "recovery"])
async def test_executor_projection_preserves_wait_and_recovery_guards(runtime, monkeypatch, guard):
    h = runtime
    queued, context, snapshot = await waiting(h, monkeypatch)
    async with h.factory() as db:
        fields = {"status": TaskStatus.RUNNING} if guard == "uncommitted_wait" else {"recovery_required": True}
        await db.execute(update(TaskModel).where(TaskModel.task_id == UUID(queued["task_id"])).values(**fields))
        await db.commit()
    _, before = await rows(h, queued["run_id"])
    with pytest.raises(DeferEvent if guard == "uncommitted_wait" else ExecutionNeedsRecovery):
        await projection.synchronize_agentic_execution(context, snapshot)
    run, after = await rows(h, queued["run_id"])
    assert run.status == AgentRunStatus.INTERRUPTED and after.last_event_sequence == before.last_event_sequence


async def test_executor_projection_retains_row_locks_through_finish_and_replays_once(runtime, monkeypatch):
    h = runtime
    queued, context, snapshot = await waiting(h, monkeypatch)
    original = TaskEventService.append_for_run
    checked = False
    async def append(db, **kwargs):
        nonlocal checked
        async with h.factory() as competing:
            await competing.execute(text("SET LOCAL lock_timeout = '100ms'"))
            with pytest.raises(DBAPIError) as error:
                await competing.execute(update(AgentRunModel)
                    .where(AgentRunModel.run_id == UUID(queued["run_id"]))
                    .values(cancel_requested_at=text("now()")))
            assert error.value.orig.sqlstate == "55P03"  # PostgreSQL lock timeout
            await competing.rollback()
        checked = True
        return await original(db, **kwargs)
    monkeypatch.setattr(TaskEventService, "append_for_run", append)
    await projection.synchronize_agentic_execution(context, snapshot)
    first_run, first_task = await rows(h, queued["run_id"])
    await projection.synchronize_agentic_execution(context, snapshot)
    run, task = await rows(h, queued["run_id"])
    assert checked and run.status == AgentRunStatus.SUCCESS and task.status == TaskStatus.SUCCESS
    assert run.completed_at == first_run.completed_at and task.last_event_sequence == first_task.last_event_sequence


async def test_executor_finish_failure_rolls_back_event_and_state_together(runtime, monkeypatch):
    h = runtime
    queued, context, snapshot = await waiting(h, monkeypatch)
    _, before = await rows(h, queued["run_id"])
    original = TaskEventService.append_for_run
    async def fail(db, **kwargs):
        await original(db, **kwargs)
        raise RuntimeError("Injected projection failure after event flush")
    monkeypatch.setattr(TaskEventService, "append_for_run", fail)
    with pytest.raises(RuntimeError, match="Injected projection failure"):
        await projection.synchronize_agentic_execution(context, snapshot)
    run, task = await rows(h, queued["run_id"])
    assert run.status == AgentRunStatus.INTERRUPTED and run.completed_at is None
    assert task.status == TaskStatus.WAITING_INPUT and task.last_event_sequence == before.last_event_sequence
