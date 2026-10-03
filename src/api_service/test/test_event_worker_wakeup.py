"""Local durable-work wakeups retain periodic recovery and failure backoff."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from api_service.worker.consumer import AckDecision,HandlerResult,StreamMessage
from api_service.worker.runtime import ExecutorWorker,_WakeAfterCommit


def runtime():
    worker=object.__new__(ExecutorWorker)
    worker.settings=SimpleNamespace(poll_seconds=.03,idle_poll_seconds=.6)
    worker._stop=asyncio.Event();worker._router_wake=asyncio.Event();worker._outbox_wake=asyncio.Event();worker.consumers=[]
    return worker


@pytest.mark.asyncio
async def test_committed_ingress_wakes_but_deferred_work_and_failure_do_not():
    wake=asyncio.Event();message=StreamMessage('1-0',{})
    class Handler:
        def lock_key(self,message):return 'same-lock'
        handle=AsyncMock(return_value=HandlerResult(AckDecision.DEFER))
    handler=Handler();wrapped=_WakeAfterCommit(handler,wake)
    assert wrapped.lock_key(message)=='same-lock'
    assert (await wrapped.handle(message)).decision==AckDecision.DEFER and not wake.is_set()
    handler.handle.return_value=HandlerResult(AckDecision.ACK)
    await wrapped.handle(message);assert wake.is_set()
    wake.clear();handler.handle.side_effect=RuntimeError('No DB commit')
    with pytest.raises(RuntimeError):await wrapped.handle(message)
    assert not wake.is_set()


@pytest.mark.asyncio
async def test_notification_during_scan_is_not_lost_and_stop_wakes_idle_loop():
    worker=runtime();wake=worker._router_wake;calls=[];next_stage=asyncio.Event()
    async def operation():
        calls.append(1)
        if len(calls)==1:
            # Durable work arrives while the scan is awaiting PostgreSQL.
            wake.set();await asyncio.sleep(0)
            return 0
        return 1
    task=asyncio.create_task(worker._loop(operation,wake=wake,after_work=next_stage.set))
    try:
        await asyncio.wait_for(next_stage.wait(),.05)
        assert len(calls)==2
        worker.request_stop();await asyncio.wait_for(task,.05)
    finally:
        task.cancel();await asyncio.gather(task,return_exceptions=True)


@pytest.mark.asyncio
async def test_unnotified_cross_process_work_is_found_by_periodic_scan():
    worker=runtime();calls=0;found=asyncio.Event()
    async def operation():
        nonlocal calls
        calls+=1
        if calls==2:found.set();worker.request_stop()
        return 0
    task=asyncio.create_task(worker._loop(operation,wake=worker._router_wake))
    try:
        await asyncio.wait_for(found.wait(),.2)
        await task;assert calls==2
    finally:
        task.cancel();await asyncio.gather(task,return_exceptions=True)


@pytest.mark.asyncio
async def test_failure_keeps_backoff_even_with_continuous_notifications():
    worker=runtime();wake=worker._router_wake;calls=0;failed=asyncio.Event()
    async def operation():
        nonlocal calls
        calls+=1;failed.set();raise RuntimeError('Unavailable database')
    task=asyncio.create_task(worker._loop(operation,wake=wake))
    try:
        await failed.wait()
        for _ in range(5):wake.set();await asyncio.sleep(.01)
        assert calls==1 # Error delay is .5s; wake traffic must not bypass it.
        worker.request_stop();await asyncio.wait_for(task,.05)
    finally:
        task.cancel();await asyncio.gather(task,return_exceptions=True)


def test_late_binding_signal_is_scoped_and_lifespan_owned():
    from api_service.worker.wakeup import binding_committed,binding_subscription
    wake=asyncio.Event();other=asyncio.Event()
    with binding_subscription('same-dsn','first',wake),binding_subscription('same-dsn','second',other):
        binding_committed('other-dsn','first');assert not wake.is_set()
        binding_committed('same-dsn','first');assert wake.is_set() and not other.is_set()
    wake.clear();binding_committed('same-dsn','first');assert not wake.is_set()


@pytest.mark.asyncio
async def test_binding_notifies_only_after_database_commit_and_return():
    from contextlib import asynccontextmanager
    from api_service.worker.store import Store
    from api_service.worker.wakeup import binding_subscription
    wake=asyncio.Event();inside=[]
    class Connection:
        @asynccontextmanager
        async def transaction(self):
            inside.append('transaction');yield;inside.remove('transaction')
        async def execute(self,*args):return self
        async def fetchone(self):return ('session','task')
    class Pool:
        conninfo='test'
        @asynccontextmanager
        async def connection(self):
            inside.append('connection');yield Connection()
            assert not wake.is_set();inside.remove('connection')
    with binding_subscription('test','test',wake):
        await Store(Pool(),'test').register(execution_id=__import__('uuid').uuid4(),session_id='session',task_id='task')
    assert wake.is_set() and inside==[]


@pytest.mark.asyncio
async def test_ignored_progress_events_do_not_trigger_extra_database_scans():
    wake=asyncio.Event()
    handler=SimpleNamespace(handle=AsyncMock(return_value=HandlerResult(AckDecision.ACK)),lock_key=lambda _:None)
    wrapped=_WakeAfterCommit(handler,wake,event_types={'execution.operation_completed','execution.completed'})
    await wrapped.handle(StreamMessage('1-0',{'event_type':'execution.step_completed'}))
    assert not wake.is_set()
    await wrapped.handle(StreamMessage('2-0',{'event_type':'execution.operation_completed'}))
    assert wake.is_set()
