"""Disposable PostgreSQL: API admission, multi-consumer claims, waits and slot timing."""
import asyncio
import json
import os
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, update

import service_settings
import app.agent_run_worker as worker
import app.services.run_service as runs
import app.services.executor_completion as completion
from app.core.enums import AgentRunStatus, TaskStatus
from app.models.common.agent_run_model import AgentRunModel
from app.models.common.task_model import TaskModel
from app.worker import EventContext, ExecutorEvent, DeferEvent
from app.test.test_user_identity_postgres import database_url, harness, add_session, headers
from app.test.test_run_cleanup_postgres import runtime, enqueue, rows


async def wait_for(predicate):
    async with asyncio.timeout(10):
        while not await predicate():
            await asyncio.sleep(.01)


async def post(h, session, body, key=None):
    return await h.client.post(f'/api/v1/sessions/{session}/runs',
        headers={**headers(h.user['user_id']), 'Idempotency-Key': key or str(uuid4())}, json=body)


@pytest.mark.asyncio
async def test_two_consumers_do_not_claim_the_same_run(runtime, monkeypatch):
    h = runtime
    queued = [await enqueue(h, await add_session(h, h.user)) for _ in range(8)]
    claims = await asyncio.gather(*(worker.claim_one() for _ in range(12)))
    claimed = [c for c in claims if c is not None]
    assert len(claimed) == len({c.claim.run_id for c in claimed}) == 8
    assert {str(c.claim.run_id) for c in claimed} == {q['id'] for q in queued}
    monkeypatch.setattr(runs, 'ainvoke_user_turn', AsyncMock(return_value={'routing_result': {'route':'analysis'}}))
    await asyncio.gather(*(worker.execute_claimed(c) for c in claimed))
    for q in queued:
        run, task = await rows(h, q['id'])
        assert run.status == AgentRunStatus.SUCCESS and run.attempt_count == 1


@pytest.mark.asyncio
async def test_same_session_concurrent_admission_and_idempotency(runtime):
    h = runtime
    body = {'input': {'messages': [{'role':'user','content':'test'}]}}
    responses = await asyncio.gather(*(post(h, h.session_id, body) for _ in range(10)))
    assert sorted(r.status_code for r in responses) == [202] + [409]*9
    other = await add_session(h, h.user)
    responses = await asyncio.gather(*(post(h, other, body, 'same-key') for _ in range(5)))
    assert all(r.status_code == 202 for r in responses)
    assert len({r.json()['id'] for r in responses}) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['USER_APPROVAL', 'EXECUTOR_EVENT'])
async def test_wait_releases_execution_but_keeps_session_admission_locked(runtime, monkeypatch, kind):
    h = runtime
    monkeypatch.setattr(runs, 'ainvoke_user_turn', AsyncMock(return_value={
        'routing_result': {'route':'analysis'}, '__interrupt__':[SimpleNamespace(value={'kind':kind})],
    }))
    queued = await enqueue(h)
    await worker.execute_claimed(await worker.claim_one())
    run, task = await rows(h, queued['id'])
    assert run.status == AgentRunStatus.INTERRUPTED
    assert task.status == TaskStatus.WAITING_INPUT and task.lock_token is None
    body = {'input': {'messages':[{'role':'user','content':'another'}]}}
    assert (await post(h, h.session_id, body)).status_code == 409
    resumes = await asyncio.gather(*(post(h,h.session_id, {'command':{'approved':True}}) for _ in range(3)))
    assert sorted(r.status_code for r in resumes) == ([409]*3 if kind=='EXECUTOR_EVENT' else [202,409,409])
    # A different session remains usable for the same user.
    assert (await post(h, await add_session(h,h.user), body)).status_code == 202


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['SUCCEEDED','FAILED'])
async def test_executor_completion_unlocks_session_once(runtime, monkeypatch, status):
    h = runtime
    graph_task, execution_id, command_id, event_id = uuid4(), uuid4(), uuid4(), uuid4()
    monkeypatch.setattr(runs, 'ainvoke_user_turn', AsyncMock(return_value={
        'routing_result': {'route':'analysis'}, '__interrupt__':[SimpleNamespace(value={'kind':'EXECUTOR_EVENT'})],
    }))
    queued = await enqueue(h)
    await worker.execute_claimed(await worker.claim_one())
    async with h.factory() as db:
        await db.execute(update(TaskModel).where(TaskModel.task_id==UUID(queued['task_id'])).values(graph_task_id=graph_task))
        await db.commit()
    monkeypatch.setattr(completion, 'get_session_factory', lambda:h.factory)
    values = {'task_id':str(graph_task),'execution_id':str(execution_id),'execution_status':status,
              'ew_receipts':{str(command_id):str(event_id)}, 'final_response':{'status':status}}
    graph = SimpleNamespace(aget_state=AsyncMock(return_value=SimpleNamespace(next=(),values=values)))
    context = EventContext('test',h.session_id,str(graph_task),execution_id,command_id,
        ExecutorEvent(event_id=event_id,execution_id=execution_id,event_type='execution.completed',event_sequence=1,
                      schema_version='1.0',occurred_at='2026-09-29T00:00:00+00:00',payload={}))
    # An event can overtake API wait persistence; the projection must defer.
    async with h.factory() as db:
        await db.execute(update(TaskModel).where(TaskModel.task_id==UUID(queued['task_id'])).values(status=TaskStatus.RUNNING))
        await db.commit()
    with pytest.raises(DeferEvent):
        await completion.synchronize_executor_completion(context, graph)
    async with h.factory() as db:
        await db.execute(update(TaskModel).where(TaskModel.task_id==UUID(queued['task_id'])).values(status=TaskStatus.WAITING_INPUT))
        await db.commit()
    await completion.synchronize_executor_completion(context, graph)
    first_run, first_task = await rows(h,queued['id'])
    await completion.synchronize_executor_completion(context, graph)
    run, task = await rows(h,queued['id'])
    assert task.last_event_sequence == first_task.last_event_sequence
    assert run.status == (AgentRunStatus.SUCCESS if status=='SUCCEEDED' else AgentRunStatus.ERROR)
    assert run.completed_at == first_run.completed_at
    assert (await post(h,h.session_id, {'input':{'messages':[{'role':'user','content':'next'}]}})).status_code == 202


@pytest.mark.asyncio
async def test_dispatcher_bounded_workload_comparison(runtime, monkeypatch):
    h = runtime
    results = []
    execute = worker.execute_claimed
    for slots in (1,2,4):
        snapshot = service_settings.get_settings()
        monkeypatch.setattr(service_settings, '_snapshot', replace(snapshot, api=snapshot.api.model_copy(update={
            'agent_worker_concurrency':slots, 'agent_worker_poll_interval_seconds':.05,
        })))
        queued = [await enqueue(h, await add_session(h,h.user)) for _ in range(20)]
        active, peak, starts = 0,0,[]
        begun = time.perf_counter()
        async def graph(*args, **kwargs):
            nonlocal active, peak
            active += 1; peak=max(peak,active); starts.append(time.perf_counter()-begun)
            try:
                await asyncio.sleep(.1)  # fixed synthetic model wait, no external call
                return {'routing_result': {'route':'analysis'}}
            finally:
                active-=1
        monkeypatch.setattr(runs, 'ainvoke_user_turn', graph)
        finished = []
        async def observed_execute(item):
            await execute(item)
            finished.append(item.claim.run_id)
        monkeypatch.setattr(worker, 'execute_claimed', observed_execute)
        ids=[UUID(q['id']) for q in queued]
        async def complete():
            async with h.factory() as db:
                states=(await db.scalars(select(AgentRunModel.status).where(AgentRunModel.run_id.in_(ids)))).all()
            return len(finished)==20 and len(states)==20 and all(s==AgentRunStatus.SUCCESS for s in states)
        dispatcher=asyncio.create_task(worker.run_forever())
        try:
            await wait_for(complete)
        finally:
            dispatcher.cancel(); await asyncio.gather(dispatcher,return_exceptions=True)
        elapsed=time.perf_counter()-begun
        assert peak<=slots and peak==slots and len(starts)==20
        results.append({'slots':slots,'runs':20,'mock_graph_wait_ms':100,'elapsed_s':round(elapsed,4),
                        'runs_per_second':round(20/elapsed,3),'peak_graph_calls':peak,
                        'mean_dispatch_to_graph_start_s':round(sum(starts)/20,4),
                        'max_dispatch_to_graph_start_s':round(max(starts),4)})
    if output:=os.getenv('DTEST_CONCURRENCY_REPORT'):
        Path(output).write_text(json.dumps(results,indent=2)+'\n')


@pytest.mark.asyncio
async def test_old_taskless_resume_cannot_bypass_executor_wait(runtime, monkeypatch):
    h = runtime
    # FAQ pauses have no analysis Task after RunService finalization.
    monkeypatch.setattr(runs, 'ainvoke_user_turn', AsyncMock(return_value={
        'routing_result':{'route':'faq'}, '__interrupt__':[SimpleNamespace(value={'kind':'NEXT_REQUEST'})],
    }))
    faq = await enqueue(h)
    await worker.execute_claimed(await worker.claim_one())
    monkeypatch.setattr(runs, 'ainvoke_user_turn', AsyncMock(return_value={
        'routing_result':{'route':'analysis'}, '__interrupt__':[SimpleNamespace(value={'kind':'EXECUTOR_EVENT'})],
    }))
    await enqueue(h)
    await worker.execute_claimed(await worker.claim_one())
    response = await post(h,h.session_id,{'command':{'message':'bypass'},'metadata':{'resume_run_id':faq['id']}})
    assert response.status_code == 409
