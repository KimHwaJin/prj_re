"""Real DB exclusion across API claims/events, cancellation and lost ownership."""
import asyncio
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, update

import service_settings
import api_service.agent_run_worker as worker
import api_service.services.session_execution as ownership
from service_contracts.execution import ExecutionNeedsRecovery
from api_service.core.execution_lifecycle import execution_health
from api_service.models.common.session_execution_model import SessionExecutionModel as Owner
from api_service.services.helpers import utc_now
from service_contracts.events import DeferEvent
from api_service.test.test_user_identity_postgres import database_url, harness, add_session
from api_service.test.test_run_cleanup_postgres import runtime, enqueue


async def until(predicate):
    async with asyncio.timeout(4):
        while not await predicate():
            await asyncio.sleep(.01)


async def row(h, session_id):
    async with h.factory() as db:
        return await db.get(Owner, UUID(str(session_id)))


def event(session_id):
    return SimpleNamespace(session_id=str(session_id), command_id=uuid4())


@pytest.mark.asyncio
async def test_api_claim_excludes_fast_event_until_full_finalization(runtime, monkeypatch):
    h = runtime
    queued = await enqueue(h)
    item = await worker.claim_one()
    original = worker._execute_claimed
    finalized, release = asyncio.Event(), asyncio.Event()
    async def execute(value):
        await original(value)
        finalized.set()
        await release.wait()
    import api_service.services.run_service as runs
    monkeypatch.setattr(runs, 'ainvoke_user_turn', AsyncMock(return_value={'routing_result':{'route':'analysis'}}))
    monkeypatch.setattr(worker, '_execute_claimed', execute)
    task = asyncio.create_task(worker.execute_claimed(item))
    call = AsyncMock()
    try:
        await finalized.wait()
        with pytest.raises(DeferEvent):
            await ownership.run_event_owned(event(h.session_id), call)
        call.assert_not_awaited()
        owner = await row(h, h.session_id)
        assert owner.owner_id == UUID(queued['run_id']) and owner.token is not None
    finally:
        release.set(); await task
    await ownership.run_event_owned(event(h.session_id), call)
    call.assert_awaited_once()
    assert (await row(h, h.session_id)).token is None


@pytest.mark.asyncio
async def test_event_blocks_same_session_claim_without_blocking_other_sessions(runtime, monkeypatch):
    h = runtime
    other = await add_session(h,h.user)
    entered, release = asyncio.Event(), asyncio.Event()
    async def graph():
        entered.set(); await release.wait()
    task = asyncio.create_task(ownership.run_event_owned(event(h.session_id), graph))
    try:
        await entered.wait()
        first = await enqueue(h)
        second = await enqueue(h,other)
        item = await worker.claim_one()
        assert item.claim.run_id == UUID(second['run_id'])
        assert await worker.claim_one() is None
    finally:
        release.set(); await task
    item = await worker.claim_one()
    assert item.claim.run_id == UUID(first['run_id']) and item.claim.attempt == 1


@pytest.mark.asyncio
async def test_duplicate_event_invocations_do_not_overlap(runtime):
    h=runtime
    entered, release = asyncio.Event(), asyncio.Event()
    async def graph():
        entered.set(); await release.wait()
    context=event(h.session_id)
    first=asyncio.create_task(ownership.run_event_owned(context,graph))
    try:
        await entered.wait()
        results=await asyncio.gather(*(ownership.run_event_owned(context,AsyncMock()) for _ in range(8)),return_exceptions=True)
        assert all(isinstance(result,DeferEvent) for result in results)
    finally:
        release.set(); await first
    # Receipt deduplication remains the adapter's job after ownership handoff.
    await ownership.run_event_owned(context,AsyncMock())


@pytest.mark.asyncio
async def test_stale_heartbeat_never_authorizes_takeover(runtime):
    h=runtime
    owner=ownership.SessionExecution(UUID(h.session_id),uuid4(),uuid4(),'executor_event')
    async with h.factory() as db:
        assert await ownership.acquire(db,owner)
        await db.execute(update(Owner).where(Owner.session_id==UUID(h.session_id)).values(heartbeat_at=utc_now()-timedelta(days=8)))
        await db.commit()
    with pytest.raises(DeferEvent):
        await ownership.run_event_owned(event(h.session_id),AsyncMock())
    assert (await row(h,h.session_id)).token==owner.token


@pytest.mark.asyncio
async def test_cancel_retains_owner_through_slow_cleanup_and_repeated_cancel(runtime):
    h=runtime
    entered, cleaning, release = asyncio.Event(),asyncio.Event(),asyncio.Event()
    async def graph():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set(); await release.wait()
    task=asyncio.create_task(ownership.run_event_owned(event(h.session_id),graph))
    try:
        await entered.wait(); task.cancel(); await cleaning.wait(); task.cancel()
        await asyncio.sleep(.02)
        assert not task.done()
        owner=await row(h,h.session_id)
        assert owner.token is not None and owner.recovery_required
        with pytest.raises(DeferEvent):
            await ownership.run_event_owned(event(h.session_id),AsyncMock())
    finally:
        release.set(); await asyncio.gather(task,return_exceptions=True)
    assert (await row(h,h.session_id)).recovery_required


@pytest.mark.asyncio
async def test_ownership_loss_stops_graph_and_cannot_release_replacement(runtime, monkeypatch):
    h=runtime
    snap=service_settings.get_settings()
    monkeypatch.setattr(service_settings,'_snapshot',replace(snap,api=snap.api.model_copy(update={'task_lease_seconds':.15})))
    entered, stopped=asyncio.Event(),asyncio.Event()
    async def graph():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    task=asyncio.create_task(ownership.run_event_owned(event(h.session_id),graph))
    await entered.wait()
    replacement=uuid4()
    async with h.factory() as db:
        # Unsupported administrative takeover, used solely to test stale release.
        await db.execute(update(Owner).where(Owner.session_id==UUID(h.session_id)).values(token=replacement))
        await db.commit()
    with pytest.raises(ExecutionNeedsRecovery):
        await asyncio.wait_for(task,3)
    assert stopped.is_set() and not execution_health.healthy
    assert (await row(h,h.session_id)).token==replacement


@pytest.mark.asyncio
async def test_event_cancel_during_acquire_waits_for_commit_then_quarantines(runtime,monkeypatch):
    h=runtime
    entered, release=asyncio.Event(),asyncio.Event()
    original=ownership.acquire
    async def acquire(db,owner):
        result=await original(db,owner)
        entered.set(); await release.wait(); return result
    monkeypatch.setattr(ownership,'acquire',acquire)
    call=AsyncMock()
    task=asyncio.create_task(ownership.run_event_owned(event(h.session_id),call))
    await entered.wait(); task.cancel(); await asyncio.sleep(.01); task.cancel()
    assert not task.done()
    release.set(); await asyncio.gather(task,return_exceptions=True)
    call.assert_not_awaited()
    owner=await row(h,h.session_id)
    assert owner.recovery_required and owner.token is not None


@pytest.mark.asyncio
async def test_confirmed_event_exception_releases_owner_for_receipt_retry(runtime):
    h=runtime
    with pytest.raises(DeferEvent,match='projection pending'):
        await ownership.run_event_owned(event(h.session_id),AsyncMock(side_effect=DeferEvent('projection pending')))
    assert (await row(h,h.session_id)).token is None
    assert execution_health.healthy
    await ownership.run_event_owned(event(h.session_id),AsyncMock())


@pytest.mark.asyncio
async def test_redis_lease_loss_cannot_unlock_live_graph(runtime):
    from api_service.worker.guard import SessionGuard, LeaseLostError
    h=runtime
    entered, cleaning, release=asyncio.Event(),asyncio.Event(),asyncio.Event()
    redis=SimpleNamespace(set=AsyncMock(return_value=True),
                          eval=AsyncMock(side_effect=lambda *_: 0 if entered.is_set() else 1))
    guard=SessionGuard(redis,'test',ttl=1,renew_seconds=.02)
    async def graph():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set(); await release.wait()
    async def dispatch():
        async with guard.hold(h.session_id):
            await ownership.run_event_owned(event(h.session_id),graph)
    task=asyncio.create_task(dispatch())
    try:
        await asyncio.wait_for(entered.wait(),4); await asyncio.wait_for(cleaning.wait(),2)
        owner=await row(h,h.session_id)
        assert owner.token is not None and owner.recovery_required
        with pytest.raises(DeferEvent):
            await ownership.run_event_owned(event(h.session_id),AsyncMock())
    finally:
        release.set()
    with pytest.raises(LeaseLostError):
        await task
    assert (await row(h,h.session_id)).token is not None


@pytest.mark.asyncio
async def test_waiting_graph_holds_no_db_connection(runtime):
    from sqlalchemy import event as sql_event
    h=runtime
    engine=h.factory.kw['bind'].sync_engine
    borrowed=[]
    sql_event.listen(engine,'checkout',lambda *_:borrowed.append(1))
    sql_event.listen(engine,'checkin',lambda *_:borrowed.pop())
    async def graph():
        assert not borrowed
        await asyncio.sleep(.01)
        assert not borrowed
    await ownership.run_event_owned(event(h.session_id),graph)
    assert not borrowed


@pytest.mark.asyncio
async def test_quarantined_session_rejects_new_api_input(runtime):
    from api_service.test.test_run_concurrency_postgres import post
    h=runtime
    owner=ownership.SessionExecution(UUID(h.session_id),uuid4(),uuid4(),'executor_event')
    async with h.factory() as db:
        assert await ownership.acquire(db,owner)
        await db.commit()
    await ownership.quarantine(owner,'test_uncertain_owner')
    response=await post(h,h.session_id,{'input':{'content': [{'type': 'text', 'text': 'next'}]}})
    assert response.status_code==409 and 'recovery' in response.text


@pytest.mark.asyncio
async def test_monitor_database_failure_stops_graph_and_retains_token(runtime,monkeypatch):
    h=runtime
    snap=service_settings.get_settings()
    monkeypatch.setattr(service_settings,'_snapshot',replace(snap,api=snap.api.model_copy(update={'task_lease_seconds':.15})))
    original=ownership._update
    count=0
    async def fail_heartbeat(owner,**values):
        nonlocal count
        count+=1
        if count==2:
            raise TimeoutError('controlled heartbeat DB timeout')
        await original(owner,**values)
    monkeypatch.setattr(ownership,'_update',fail_heartbeat)
    stopped=asyncio.Event()
    async def graph():
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    with pytest.raises(ExecutionNeedsRecovery):
        await asyncio.wait_for(ownership.run_event_owned(event(h.session_id),graph),3)
    assert stopped.is_set()
    owner=await row(h,h.session_id)
    assert owner.token is not None and owner.recovery_required


@pytest.mark.asyncio
async def test_legacy_running_or_recovery_task_blocks_event_without_owner_row(runtime):
    from api_service.models.common.task_model import TaskModel
    from api_service.core.enums import TaskStatus
    h=runtime
    queued=await enqueue(h)
    call=AsyncMock()
    for status,recovering in [(TaskStatus.RUNNING,False),(TaskStatus.WAITING_INPUT,True)]:
        async with h.factory() as db:
            await db.execute(update(TaskModel).where(TaskModel.task_id==UUID(queued['task_id'])).values(status=status,recovery_required=recovering))
            await db.commit()
        with pytest.raises(DeferEvent):
            await ownership.run_event_owned(event(h.session_id),call)
    call.assert_not_awaited()
    assert await row(h,h.session_id) is None


@pytest.mark.asyncio
async def test_real_checkpoint_cannot_resume_before_owner_handoff(runtime):
    from sqlalchemy.engine import make_url
    from langgraph.types import Command
    from agent_service.runtime.langgraph.checkpointer import create_checkpointer
    from api_service.test.test_graph_runtime_postgres import checkpoint_graph
    h=runtime
    url=make_url(service_settings.get_settings().api.database_url).set(drivername='postgresql').render_as_string(hide_password=False)
    owner=ownership.SessionExecution(UUID(h.session_id),uuid4(),uuid4(),'api_run')
    async with h.factory() as db:
        assert await ownership.acquire(db,owner)
        await db.commit()
    config={'configurable':{'thread_id':h.session_id}}
    async with create_checkpointer(url,setup_on_start=True,min_size=1,max_size=2) as saver:
        graph=checkpoint_graph(saver)
        paused,release=asyncio.Event(),asyncio.Event()
        async def initial():
            result=await graph.ainvoke({'label':'shared-owner'},config,durability='sync')
            assert result['__interrupt__']
            paused.set(); await release.wait()
        task=asyncio.create_task(ownership.run_owned(owner,initial))
        try:
            await paused.wait()
            before=await graph.aget_state(config)
            with pytest.raises(DeferEvent):
                await ownership.run_event_owned(event(h.session_id),lambda:graph.ainvoke(Command(resume='answer'),config,durability='sync'))
            blocked=await graph.aget_state(config)
            assert blocked.config==before.config and blocked.values==before.values
        finally:
            release.set(); await task
        result=await ownership.run_event_owned(event(h.session_id),lambda:graph.ainvoke(Command(resume='answer'),config,durability='sync'))
        assert result=={'label':'shared-owner','answer':'answer'}


@pytest.mark.asyncio
async def test_fast_executor_waits_for_short_api_handoff_without_retaining_db_connection(runtime,monkeypatch):
    h=runtime
    queued=await enqueue(h);item=await worker.claim_one()
    original=worker._execute_claimed
    finalized,release=asyncio.Event(),asyncio.Event()
    async def execute(value):
        await original(value);finalized.set();await release.wait()
    import api_service.services.run_service as runs
    monkeypatch.setattr(runs,'ainvoke_user_turn',AsyncMock(return_value={'routing_result':{'route':'analysis'}}))
    monkeypatch.setattr(worker,'_execute_claimed',execute)
    api=asyncio.create_task(worker.execute_claimed(item));call=AsyncMock()
    event_task=None
    from sqlalchemy import event as sa_event
    checkouts=[]
    def checked_out(*args):checkouts.append(1)
    def checked_in(*args):checkouts.pop()
    sa_event.listen(h.engine.sync_engine,'checkout',checked_out)
    sa_event.listen(h.engine.sync_engine,'checkin',checked_in)
    handoff_wait=asyncio.Event();original_sleep=asyncio.sleep
    async def measured_sleep(seconds):
        if asyncio.current_task().get_name()==f'executor-event-admission:{h.session_id}':
            assert not checkouts
            handoff_wait.set()
        await original_sleep(seconds)
    monkeypatch.setattr(ownership.asyncio,'sleep',measured_sleep)
    try:
        await finalized.wait()
        event_task=asyncio.create_task(ownership.run_event_owned(event(h.session_id),call,handoff_timeout_seconds=.5))
        await asyncio.wait_for(handoff_wait.wait(),.4)
        call.assert_not_awaited()
        # No connection/transaction is retained while waiting for API ownership.
        assert not checkouts
        assert (await row(h,h.session_id)).token is not None
        release.set();await api;await asyncio.wait_for(event_task,.5)
        call.assert_awaited_once();assert (await row(h,h.session_id)).token is None
    finally:
        release.set();await asyncio.gather(api,return_exceptions=True)
        if event_task:
            event_task.cancel();await asyncio.gather(event_task,return_exceptions=True)
        sa_event.remove(h.engine.sync_engine,'checkout',checked_out)
        sa_event.remove(h.engine.sync_engine,'checkin',checked_in)


@pytest.mark.asyncio
async def test_bounded_handoff_never_steals_a_live_owner(runtime):
    h=runtime;owner=ownership.SessionExecution(UUID(h.session_id),uuid4(),uuid4(),'api_run')
    async with h.factory() as db:
        assert await ownership.acquire(db,owner);await db.commit()
    call=AsyncMock()
    with pytest.raises(DeferEvent):
        await ownership.run_event_owned(event(h.session_id),call,handoff_timeout_seconds=.05)
    call.assert_not_awaited();assert (await row(h,h.session_id)).token==owner.token
