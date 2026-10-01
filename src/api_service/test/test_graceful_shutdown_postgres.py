"""Drain actual Run/Task/ownership lifecycle on disposable PostgreSQL."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID
from unittest.mock import AsyncMock

import pytest

import service_settings
import api_service.agent_run_worker as worker
import api_service.services.run_service as runs
from api_service.core.enums import AgentRunStatus, TaskStatus
from api_service.core.execution_lifecycle import execution_health
from api_service.models.common.session_execution_model import SessionExecutionModel
from service_bootstrap import BackgroundRuntime
from api_service.test.test_user_identity_postgres import database_url,harness,add_session
from api_service.test.test_run_cleanup_postgres import runtime,enqueue,rows


async def until(check):
    async with asyncio.timeout(5):
        while not check():await asyncio.sleep(.01)


async def owner(h,session_id):
    async with h.factory() as db:
        return await db.get(SessionExecutionModel,UUID(str(session_id)))


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',[None,'USER_APPROVAL','EXECUTOR_EVENT'])
async def test_drain_finishes_active_runs_releases_owners_and_leaves_queue(runtime,monkeypatch,kind):
    h=runtime
    snapshot=service_settings.get_settings()
    monkeypatch.setattr(service_settings,'_snapshot',replace(snapshot,api=snapshot.api.model_copy(update={
        'agent_worker_concurrency':2,'agent_worker_poll_interval_seconds':.05,
    })))
    sessions=[await add_session(h,h.user) for _ in range(3)]
    queued=[await enqueue(h,s) for s in sessions]
    entered=[];release=asyncio.Event();canceled=[]
    async def graph(*args,**kwargs):
        entered.append(kwargs['session_id'])
        try:await release.wait()
        except asyncio.CancelledError:canceled.append(True);raise
        result={'routing_result':{'route':'analysis'}}
        if kind:result['__interrupt__']=[SimpleNamespace(value={'kind':kind})]
        return result
    monkeypatch.setattr(runs,'ainvoke_user_turn',graph)
    stop=asyncio.Event()
    background=BackgroundRuntime({'runs':lambda:worker.run_forever(stop_event=stop)},2,
                                 stop_event=stop,drain_timeout=2)
    await background.start()
    try:
        await until(lambda:len(entered)==2)
        background.request_stop()
        assert not background.ready
        await asyncio.sleep(.03)
        assert len(entered)==2 and not canceled
        release.set();await background.stop()
    finally:
        release.set()
        await background.stop()
    for index in range(2):
        run,task=await rows(h,queued[index]['run_id'])
        assert run.status==(AgentRunStatus.INTERRUPTED if kind else AgentRunStatus.SUCCESS)
        assert task.status==(TaskStatus.WAITING_INPUT if kind else TaskStatus.SUCCESS)
        record=await owner(h,sessions[index])
        assert record.token is None and not record.recovery_required
    pending,task=await rows(h,queued[2]['run_id'])
    assert pending.status==AgentRunStatus.PENDING and pending.attempt_count==0
    assert await owner(h,sessions[2]) is None
    assert execution_health.healthy and not canceled


@pytest.mark.asyncio
async def test_deadline_exceeded_retains_recovery_ownership(runtime,monkeypatch):
    h=runtime
    queued=await enqueue(h)
    entered,stopped=asyncio.Event(),asyncio.Event()
    async def graph(*args,**kwargs):
        entered.set()
        try:await asyncio.Event().wait()
        finally:stopped.set()
    monkeypatch.setattr(runs,'ainvoke_user_turn',graph)
    stop=asyncio.Event()
    background=BackgroundRuntime({'runs':lambda:worker.run_forever(stop_event=stop)},2,
                                 stop_event=stop,drain_timeout=.1)
    await background.start();await asyncio.wait_for(entered.wait(),5)
    await background.stop()
    run,task=await rows(h,queued['run_id'])
    record=await owner(h,h.session_id)
    assert stopped.is_set() and record.token is not None and record.recovery_required
    assert task.recovery_required and not execution_health.healthy
    assert run.status==AgentRunStatus.RUNNING


@pytest.mark.asyncio
async def test_stop_during_committing_claim_finishes_owned_call_normally(runtime,monkeypatch):
    h=runtime
    queued=await enqueue(h)
    acquired,release=asyncio.Event(),asyncio.Event()
    claim=worker.claim_one
    async def delayed_claim():
        item=await claim()
        acquired.set();await release.wait();return item
    monkeypatch.setattr(worker,'claim_one',delayed_claim)
    graph=AsyncMock(return_value={'routing_result':{'route':'analysis'}})
    monkeypatch.setattr(runs,'ainvoke_user_turn',graph)
    stop=asyncio.Event()
    background=BackgroundRuntime({'runs':lambda:worker.run_forever(stop_event=stop)},2,
                                 stop_event=stop,drain_timeout=2)
    await background.start();await asyncio.wait_for(acquired.wait(),5)
    background.request_stop();release.set();await background.stop()
    run,task=await rows(h,queued['run_id'])
    assert run.status==AgentRunStatus.SUCCESS and not task.recovery_required
    assert (await owner(h,h.session_id)).token is None
    graph.assert_awaited_once()
    assert execution_health.healthy
