"""Run lifecycle tests against the same DISPOSABLE DB guard as identity tests.

Real API, queue claims, cancellation polling, heartbeat/recovery storage;
graph execution is controlled locally. No LLM/Executor is contacted.
"""
import asyncio
from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select, update

import service_settings
import api_service.core.database as database
import api_service.agent_run_worker as worker
import api_service.services.run_service as runs
import api_service.services.task_service as tasks
import api_service.services.llm_token_event_service as tokens
from api_service.core.enums import AgentRunStatus, TaskStatus
from service_contracts.execution import ExecutionNeedsRecovery
from api_service.core.execution_lifecycle import execution_health
from api_service.models.common.agent_run_model import AgentRunModel
from api_service.models.common.task_model import TaskModel
from api_service.services.helpers import utc_now
from api_service.services.run_service import RunService
from api_service.services.task_service import TaskService
from api_service.test.test_user_identity_postgres import database_url, harness, initialize, add_user, add_session, headers


@pytest_asyncio.fixture
async def runtime(harness, monkeypatch):
    h = harness
    configured = service_settings.get_settings()
    monkeypatch.setattr(service_settings, '_snapshot', replace(configured, api=configured.api.model_copy(update={
        'run_cleanup_timeout_seconds': .15, 'run_monitor_timeout_seconds': 1,
        'task_cancel_poll_interval_seconds': .05, 'agent_worker_retry_backoff_seconds': 0,
    })))
    monkeypatch.setattr(execution_health, 'faults', {})
    monkeypatch.setattr(execution_health, 'recorders', set())
    for module in (database, worker, runs, tasks, tokens):
        monkeypatch.setattr(module, 'get_session_factory', lambda: h.factory)
    await initialize(h)
    h.user = await add_user(h)
    h.session_id = await add_session(h, h.user)
    yield h
    if execution_health.recorders:
        await asyncio.gather(*execution_health.recorders)


async def enqueue(h, session_id=None):
    session_id = session_id or h.session_id
    response = await h.client.post(f'/api/v1/sessions/{session_id}/runs',
        headers={**headers(h.user['user_id']), 'Idempotency-Key': str(uuid4())},
        json={'input': {'content': [{'type': 'text', 'text': 'test request'}]}})
    assert response.status_code == 202, response.text
    return response.json()


async def rows(h, run_id):
    async with h.factory() as db:
        # One statement snapshot: two READ COMMITTED SELECTs can straddle the
        # recorder commit and combine old Run.failure with new Task guard.
        result = await db.execute(select(AgentRunModel, TaskModel)
            .join(TaskModel, TaskModel.task_id == AgentRunModel.task_id)
            .where(AgentRunModel.public_run_id == UUID(str(run_id)))
            .order_by(AgentRunModel.created_at.desc(), AgentRunModel.run_id.desc()).limit(1))
        return result.one()


async def wait_until_recovery(h, run_id):
    async with asyncio.timeout(2):
        while True:
            run, task = await rows(h, run_id)
            if task.recovery_required:
                return run, task
            await asyncio.sleep(.02)


@pytest.mark.asyncio
async def test_api_claim_graph_completion(runtime, monkeypatch):
    h = runtime
    monkeypatch.setattr(runs, 'ainvoke_user_turn', AsyncMock(return_value={'routing_result': {'route': 'analysis'}}))
    queued = await enqueue(h)
    item = await worker.claim_one()
    await worker.execute_claimed(item)
    run, task = await rows(h, queued['run_id'])
    assert run.status == AgentRunStatus.SUCCESS and run.attempt_count == 1
    assert task.status == TaskStatus.SUCCESS and task.lock_token is None
    assert not task.recovery_required and execution_health.healthy
    assert await worker.claim_one() is None


@pytest.mark.asyncio
async def test_confirmed_graph_error_keeps_existing_retry_policy(runtime, monkeypatch):
    h = runtime
    monkeypatch.setattr(runs, 'ainvoke_user_turn', AsyncMock(side_effect=ValueError('graph fault')))
    queued = await enqueue(h)
    await worker.execute_claimed(await worker.claim_one())
    run, task = await rows(h, queued['run_id'])
    assert run.status == AgentRunStatus.PENDING and run.failure['retry_scheduled']
    assert task.status == TaskStatus.PENDING and not task.recovery_required
    assert execution_health.healthy
    assert await worker.claim_one() is not None


@pytest.mark.asyncio
async def test_actual_cancel_api_waits_for_graph_ack(runtime, monkeypatch):
    h = runtime
    started, cleaning, release = [asyncio.Event() for _ in range(3)]
    async def graph(*_, **__):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
    monkeypatch.setattr(runs, 'ainvoke_user_turn', graph)
    queued = await enqueue(h)
    owner = asyncio.create_task(worker.execute_claimed(await worker.claim_one()))
    try:
        await asyncio.wait_for(started.wait(), 2)
        response = await h.client.post(f"/api/v1/sessions/{h.session_id}/runs/{queued['run_id']}/cancel",
                                      headers=headers(h.user['user_id']), json={})
        assert response.status_code == 202
        await asyncio.wait_for(cleaning.wait(), 2)
        run, task = await rows(h, queued['run_id'])
        assert run.status == AgentRunStatus.RUNNING and task.lock_token is not None
    finally:
        release.set()
        await asyncio.wait_for(owner, 2)
    run, task = await rows(h, queued['run_id'])
    assert run.status == AgentRunStatus.CANCELED and task.status == TaskStatus.CANCELED
    assert task.lock_token is None and not task.recovery_required
    assert execution_health.healthy


@pytest.mark.asyncio
async def test_stuck_watcher_is_durable_visible_and_session_stays_locked(runtime, monkeypatch):
    h = runtime
    release, started = asyncio.Event(), asyncio.Event()
    async def watcher(_id, _stop):
        started.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                pass
        return False
    async def graph(*_, **__):
        await started.wait()
        return {'routing_result': {'route': 'analysis'}}
    monkeypatch.setattr(RunService, '_wait_for_cancellation', watcher)
    monkeypatch.setattr(runs, 'ainvoke_user_turn', graph)
    queued = await enqueue(h)
    owner = asyncio.create_task(worker.execute_claimed(await worker.claim_one()))
    try:
        run, task = await wait_until_recovery(h, queued['run_id'])
        assert run.failure['code'] == 'RUN_RECOVERY_REQUIRED' and not run.failure['retry_scheduled']
        assert run.status == AgentRunStatus.RUNNING and task.status == TaskStatus.RUNNING
        assert not owner.done() and not execution_health.healthy
        response = await h.client.post(f'/api/v1/sessions/{h.session_id}/runs',
            headers={**headers(h.user['user_id']), 'Idempotency-Key': str(uuid4())},
            json={'input': {'content': [{'type': 'text', 'text': 'second'}]}})
        assert response.status_code == 409
        response = await h.client.get(f'/api/v1/sessions/{h.session_id}/tasks', headers=headers(h.user['user_id']))
        assert response.status_code == 200 and response.json()['items'][0]['recovery_required'] is True
        async with h.factory() as db:
            with pytest.raises(ExecutionNeedsRecovery):
                await RunService._lock_run_and_task(db, UUID(queued['run_id']))
        response = await h.client.post(f'/api/v1/sessions/{h.session_id}/runs/{queued["run_id"]}/cancel',
            headers=headers(h.user['user_id']), json={})
        assert response.status_code == 409
        with pytest.raises(ExecutionNeedsRecovery):
            await worker.claim_one()
    finally:
        release.set()
        with pytest.raises(ExecutionNeedsRecovery):
            await asyncio.wait_for(owner, 2)
    run, task = await rows(h, queued['run_id'])
    assert task.recovery_required and run.status == AgentRunStatus.RUNNING
    assert run.failure['message'] == 'cancel_watch_stop'


@pytest.mark.asyncio
async def test_expired_lease_quarantines_not_requeues_and_preserves_cancel(runtime):
    h = runtime
    queued = await enqueue(h)
    await worker.claim_one()
    past = utc_now() - timedelta(seconds=10)
    async with h.factory() as db:
        await db.execute(update(TaskModel).values(lease_expires_at=past, cancel_requested_at=past))
        await db.execute(update(AgentRunModel).values(cancel_requested_at=past))
        await db.commit()
    before_run, before_task = await rows(h, queued['run_id'])
    async with h.factory() as db:
        assert await TaskService.reconcile_stale(db) == [before_task.task_id]
        assert await TaskService.reconcile_stale(db) == []
        assert not await TaskService.heartbeat(db, task_id=before_task.task_id, lock_token=before_task.lock_token)
    run, task = await rows(h, queued['run_id'])
    assert run.status == AgentRunStatus.RUNNING and run.attempt_count == 1
    assert task.status == TaskStatus.RUNNING and task.recovery_required
    assert task.cancel_requested_at == run.cancel_requested_at == past
    assert task.lock_token == before_task.lock_token and task.completed_at is None
    assert await worker.claim_one() is None  # independent healthy worker cannot replay it


@pytest.mark.asyncio
async def test_queued_wait_is_not_expired_execution(runtime):
    h = runtime
    queued = await enqueue(h)
    async with h.factory() as db:
        await db.execute(update(TaskModel).values(lease_expires_at=utc_now() - timedelta(days=1)))
        await db.commit()
        assert await TaskService.reconcile_stale(db) == []
    run, task = await rows(h, queued['run_id'])
    assert run.status == AgentRunStatus.PENDING and not task.recovery_required
    assert await worker.claim_one() is not None


@pytest.mark.asyncio
async def test_late_graph_success_cannot_overwrite_recovery(runtime, monkeypatch):
    h = runtime
    async def graph(*_, run_id, **__):
        async with h.factory() as db:
            assert await TaskService.require_recovery(db, run_id=run_id, reason='owner_uncertain')
        return {'routing_result': {'route': 'analysis'}}
    monkeypatch.setattr(runs, 'ainvoke_user_turn', graph)
    queued = await enqueue(h)
    with pytest.raises(ExecutionNeedsRecovery):
        await worker.execute_claimed(await worker.claim_one())
    run, task = await rows(h, queued['run_id'])
    assert task.recovery_required and run.status == AgentRunStatus.RUNNING


@pytest.mark.asyncio
async def test_shutdown_cancellation_persists_recovery_after_graph_stop(runtime, monkeypatch):
    h = runtime
    started, stopped = asyncio.Event(), asyncio.Event()
    async def graph(*_, **__):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    monkeypatch.setattr(runs, 'ainvoke_user_turn', graph)
    queued = await enqueue(h)
    owner = asyncio.create_task(worker.execute_claimed(await worker.claim_one()))
    await asyncio.wait_for(started.wait(), 2)
    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(owner, 2)
    run, task = await rows(h, queued['run_id'])
    assert stopped.is_set() and task.recovery_required
    assert run.status == AgentRunStatus.RUNNING and not run.failure['retry_scheduled']


@pytest.mark.asyncio
async def test_concurrent_reconcilers_do_not_duplicate_or_release_claims(runtime):
    h = runtime
    for _ in range(6):
        session_id = await add_session(h, h.user)
        await enqueue(h, session_id)
        await worker.claim_one()
    async with h.factory() as db:
        await db.execute(update(TaskModel).values(lease_expires_at=utc_now() - timedelta(seconds=1)))
        await db.commit()
    async def reconcile():
        async with h.factory() as db:
            return await TaskService.reconcile_stale(db, batch_size=3)
    results = await asyncio.wait_for(asyncio.gather(reconcile(), reconcile()), 2)
    combined = results[0] + results[1]
    assert len(combined) == len(set(combined)) == 6
    assert await worker.claim_one() is None


@pytest.mark.asyncio
async def test_orphaned_running_task_is_flagged_without_unlock(runtime):
    h = runtime
    async with h.factory() as db:
        task = TaskService.create_model(session_id=UUID(h.session_id), idempotency_key=str(uuid4()), owner='lost-owner')
        task.lease_expires_at = utc_now() - timedelta(seconds=1)
        db.add(task)
        await db.commit()
        task_id, token = task.task_id, task.lock_token
        assert await TaskService.reconcile_stale(db) == [task_id]
    async with h.factory() as db:
        task = await db.get(TaskModel, task_id)
        assert task.recovery_required and task.status == TaskStatus.RUNNING
        assert task.lock_token == token and task.completed_at is None


@pytest.mark.asyncio
async def test_hitl_resume_keeps_normal_lifecycle(runtime, monkeypatch):
    from types import SimpleNamespace
    h = runtime
    monkeypatch.setattr(runs, 'ainvoke_user_turn', AsyncMock(return_value={
        'routing_result': {'route': 'analysis'},
        '__interrupt__': [SimpleNamespace(value={'question': 'approve?'}, id='test-hitl')],
    }))
    monkeypatch.setattr(runs, 'ainvoke_resume', AsyncMock(return_value={'routing_result': {'route': 'analysis'}}))
    queued = await enqueue(h)
    await worker.execute_claimed(await worker.claim_one())
    run, task = await rows(h, queued['run_id'])
    assert run.status == AgentRunStatus.INTERRUPTED and task.status == TaskStatus.WAITING_INPUT
    response = await h.client.post(f'/api/v1/sessions/{h.session_id}/runs',
        headers={**headers(h.user['user_id']), 'Idempotency-Key': str(uuid4())},
        json={'run_id':queued['run_id'], 'command': {'resume': {'action':'approve_plan', 'plan_id':'test-plan', 'plan_revision':1}}, 'resume_token': str(run.run_id)})
    assert response.status_code == 202, response.text
    resumed = response.json()
    await worker.execute_claimed(await worker.claim_one())
    run, finished_task = await rows(h, resumed['run_id'])
    assert run.status == AgentRunStatus.SUCCESS and finished_task.status == TaskStatus.SUCCESS
    assert finished_task.task_id == task.task_id and not finished_task.recovery_required
    assert execution_health.healthy
