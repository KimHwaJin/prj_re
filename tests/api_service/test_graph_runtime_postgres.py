"""Real checkpoint pool/HITL and immutable claim tests on disposable PostgreSQL."""
import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from dataclasses import replace
from typing import TypedDict
from uuid import UUID, uuid4
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import update
from sqlalchemy.engine import make_url
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command

import service_settings
import api_service.workers.agent as worker
import api_service.runs.execution as runs
from service_contracts.execution import ExecutionNeedsRecovery
from api_service.runs.lifecycle import execution_health
from api_service.models.enums import AgentRunStatus, TaskStatus
from agent_service.runtime.langgraph.checkpointer import create_checkpointer
from api_service.models.task_model import TaskModel
from api_service.models.agent_run_model import AgentRunModel
from api_service.runs.runtime import AgentGraphRuntime
from api_service.runs.tasks import TaskService
from api_service.utils import utc_now
from tests.api_service.test_run_cleanup_postgres import runtime, enqueue, rows
from tests.api_service.test_user_identity_postgres import database_url, harness


class ApprovalState(TypedDict):
    label: str
    answer: str


def checkpoint_graph(saver):
    async def approval(state):
        return {'answer': interrupt({'label': state['label']})}
    return StateGraph(ApprovalState).add_node('approval', approval).add_edge(START, 'approval').add_edge('approval', END).compile(checkpointer=saver)


@pytest.mark.asyncio
async def test_real_pool_reused_across_sessions_hitl_and_restarted_runtime(harness, monkeypatch):
    h = harness
    url = make_url(service_settings.get_settings().api.database_url).set(drivername='postgresql').render_as_string(hide_password=False)
    created, pools = [], []
    runtime = AgentGraphRuntime()
    @asynccontextmanager
    async def context():
        async with create_checkpointer(database_url=url, setup_on_start=True, min_size=1, max_size=2, timeout=2) as saver:
            created.append(saver)
            pools.append(saver.conn)
            yield checkpoint_graph(saver)
    monkeypatch.setattr(runtime, '_graph_context', context)
    configs = [{'configurable': {'thread_id': str(uuid4())}} for _ in range(20)]
    try:
        async def start(index):
            async with runtime.open_graph() as graph:
                result = await graph.ainvoke({'label': f'user-{index}'}, configs[index])
                assert result['__interrupt__'][0].value == {'label': f'user-{index}'}
        await asyncio.gather(*(start(i) for i in range(20)))
        assert len(created) == 1
        stats = pools[0].get_stats()
        assert stats['pool_size'] <= 2 and stats['pool_available'] == stats['pool_size']
        assert not pools[0].closed  # no connection is held for paused sessions
        # Several resumes share the same graph; one paused session survives restart.
        async def resume(index):
            async with runtime.open_graph() as graph:
                result = await graph.ainvoke(Command(resume=f'answer-{index}'), configs[index])
                assert result == {'label': f'user-{index}', 'answer': f'answer-{index}'}
        await asyncio.gather(*(resume(i) for i in range(19)))
        assert len(created) == 1
        await runtime.shutdown()
        assert pools[0].closed
        runtime.start()
        await resume(19)
        assert len(created) == 2 and pools[1] is not pools[0]
        async with runtime.open_graph() as graph:
            for i in range(20):
                snapshot = await graph.aget_state(configs[i])
                assert snapshot.values == {'label': f'user-{i}', 'answer': f'answer-{i}'}
        stats = pools[1].get_stats()
        assert stats['pool_available'] == stats['pool_size']
    finally:
        await runtime.shutdown()
    assert all(pool.closed for pool in pools)


async def change_owner(h, item):
    token = uuid4()
    async with h.factory() as db:
        await db.execute(update(TaskModel).where(TaskModel.task_id == item.claim.task_id).values(
            lock_token=token, lease_expires_at=utc_now() + timedelta(minutes=5)))
        await db.execute(update(AgentRunModel).where(AgentRunModel.run_id == item.claim.run_id).values(
            attempt_count=item.claim.attempt + 1))
        await db.commit()
    return token


async def drain_recorders():
    if execution_health.recorders:
        await asyncio.gather(*execution_health.recorders)


@pytest.mark.asyncio
async def test_stale_claim_rejected_before_graph_without_quarantining_new_owner(runtime, monkeypatch):
    h = runtime
    graph = AsyncMock(return_value={'routing_result': {'route': 'analysis'}})
    monkeypatch.setattr(runs, 'ainvoke_user_turn', graph)
    queued = await enqueue(h)
    item = await worker.claim_one()
    new_token = await change_owner(h, item)
    with pytest.raises(ExecutionNeedsRecovery):
        await worker.execute_claimed(item)
    await drain_recorders()
    graph.assert_not_awaited()
    run, task = await rows(h, queued['run_id'])
    assert run.status == AgentRunStatus.RUNNING and run.attempt_count == item.claim.attempt + 1
    assert task.lock_token == new_token and not task.recovery_required and run.failure is None


@pytest.mark.asyncio
async def test_expired_claim_never_starts_graph(runtime, monkeypatch):
    h = runtime
    graph = AsyncMock()
    monkeypatch.setattr(runs, 'ainvoke_user_turn', graph)
    queued = await enqueue(h)
    item = await worker.claim_one()
    async with h.factory() as db:
        await db.execute(update(TaskModel).where(TaskModel.task_id == item.claim.task_id).values(
            lease_expires_at=utc_now() - timedelta(seconds=1)))
        await db.commit()
    with pytest.raises(ExecutionNeedsRecovery):
        await worker.execute_claimed(item)
    await drain_recorders()
    graph.assert_not_awaited()
    _, task = await rows(h, queued['run_id'])
    assert task.recovery_required


@pytest.mark.asyncio
async def test_late_success_cannot_finish_new_owners_run(runtime, monkeypatch):
    h = runtime
    queued = await enqueue(h)
    item = await worker.claim_one()
    async def graph(*_, **__):
        await change_owner(h, item)
        return {'routing_result': {'route': 'analysis'}}
    monkeypatch.setattr(runs, 'ainvoke_user_turn', graph)
    with pytest.raises(ExecutionNeedsRecovery):
        await worker.execute_claimed(item)
    await drain_recorders()
    run, task = await rows(h, queued['run_id'])
    assert run.status == AgentRunStatus.RUNNING and task.status == TaskStatus.RUNNING
    assert not task.recovery_required and task.completed_at is None


@pytest.mark.asyncio
async def test_heartbeat_uses_captured_token_after_owner_changes(runtime, monkeypatch):
    h = runtime
    configured = service_settings.get_settings()
    monkeypatch.setattr(service_settings, '_snapshot', replace(configured, api=configured.api.model_copy(update={'task_lease_seconds': 3})))
    queued = await enqueue(h)
    item = await worker.claim_one()
    stopped = asyncio.Event()
    async def graph(*_, **__):
        await change_owner(h, item)
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    monkeypatch.setattr(runs, 'ainvoke_user_turn', graph)
    with pytest.raises(ExecutionNeedsRecovery):
        await asyncio.wait_for(worker.execute_claimed(item), 3)
    await drain_recorders()
    run, task = await rows(h, queued['run_id'])
    assert stopped.is_set() and not task.recovery_required
    assert run.attempt_count == item.claim.attempt + 1
    async with h.factory() as db:
        assert not await TaskService.heartbeat(db, task_id=task.task_id, lock_token=item.claim.lock_token)


@pytest.mark.asyncio
async def test_replaying_completed_claim_does_not_execute_graph_again(runtime, monkeypatch):
    h = runtime
    graph = AsyncMock(return_value={'routing_result': {'route': 'analysis'}})
    monkeypatch.setattr(runs, 'ainvoke_user_turn', graph)
    queued = await enqueue(h)
    item = await worker.claim_one()
    await worker.execute_claimed(item)
    with pytest.raises(ExecutionNeedsRecovery):
        await worker.execute_claimed(item)
    await drain_recorders()
    assert graph.await_count == 1
    run, task = await rows(h, queued['run_id'])
    assert run.status == AgentRunStatus.SUCCESS and not task.recovery_required
