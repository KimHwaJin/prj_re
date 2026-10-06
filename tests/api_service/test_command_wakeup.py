"""Scheduler races and shared-listener lifetime without external services."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio

import service_settings
from api_service.workers import agent as worker
from api_service.runs.lifecycle import execution_health
from service_runtime.postgres_signals import PostgresSignals, RUN_CHANNEL, COMMAND_CHANNEL


@pytest_asyncio.fixture
async def scheduler(monkeypatch):
    monkeypatch.setattr(service_settings, '_snapshot', None)
    service_settings.configure(service_settings.load_settings(config={
        'AGENT_WORKER_CONCURRENCY':2, 'AGENT_WORKER_POLL_INTERVAL_SECONDS':.05,
        'AGENT_WORKER_RECONCILE_INTERVAL_SECONDS':10}, environ={}))
    monkeypatch.setattr(execution_health, 'faults', {})
    signals = SimpleNamespace(ready=asyncio.Event(), wake=None)
    signals.ready.set()
    @asynccontextmanager
    async def listen(_url, _namespace, wake):
        signals.wake = wake
        yield signals
    monkeypatch.setattr(worker, 'subscription', listen)
    monkeypatch.setattr(worker, 'next_retry_delay', AsyncMock(return_value=None))
    return signals


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(.001)


async def stop(task):
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def item():
    return SimpleNamespace(command_id=uuid4())


@pytest.mark.asyncio
async def test_healthy_idle_listener_does_not_repeat_empty_claim(scheduler,monkeypatch):
    claim=AsyncMock(return_value=None)
    monkeypatch.setattr(worker,'claim_one',claim)
    task=asyncio.create_task(worker.run_forever())
    try:
        await until(lambda:claim.await_count==1)
        await asyncio.sleep(.16)
        assert claim.await_count==1
    finally:
        await stop(task)


@pytest.mark.asyncio
async def test_commit_hint_during_empty_claim_cannot_be_cleared_before_sleep(scheduler,monkeypatch):
    entered=asyncio.Event(); finished=asyncio.Event(); calls=0
    value=item()
    async def claim():
        nonlocal calls
        calls+=1
        if calls==1:
            scheduler.wake.set()  # Commit races between SELECT and return.
            return None
        return value if calls==2 else None
    async def execute(_):
        entered.set(); await finished.wait()
    monkeypatch.setattr(worker,'claim_one',claim)
    monkeypatch.setattr(worker,'execute_claimed',execute)
    task=asyncio.create_task(worker.run_forever())
    try:
        await asyncio.wait_for(entered.wait(),.5)
    finally:
        finished.set(); await stop(task)


@pytest.mark.asyncio
async def test_full_capacity_coalesces_hints_without_claim_or_busy_loop(scheduler,monkeypatch):
    started=[]; release=asyncio.Event()
    claim=AsyncMock(side_effect=item)
    async def execute(value):
        started.append(value); await release.wait()
    monkeypatch.setattr(worker,'claim_one',claim)
    monkeypatch.setattr(worker,'execute_claimed',execute)
    task=asyncio.create_task(worker.run_forever())
    try:
        await until(lambda:len(started)==2)
        for _ in range(100):scheduler.wake.set()
        await asyncio.sleep(.1)
        assert claim.await_count==2 and len(started)==2
    finally:
        await stop(task)


@pytest.mark.asyncio
async def test_due_timer_rechecks_without_a_second_notification(scheduler,monkeypatch):
    claim=AsyncMock(return_value=None)
    due=AsyncMock(side_effect=[.07,None])
    monkeypatch.setattr(worker,'claim_one',claim)
    monkeypatch.setattr(worker,'next_retry_delay',due)
    task=asyncio.create_task(worker.run_forever())
    try:
        await until(lambda:claim.await_count==2)
        await asyncio.sleep(.08)
        assert claim.await_count==2
    finally:
        await stop(task)


@pytest.mark.asyncio
async def test_listener_loss_returns_to_short_fallback_poll(scheduler,monkeypatch):
    claim=AsyncMock(return_value=None)
    monkeypatch.setattr(worker,'claim_one',claim)
    task=asyncio.create_task(worker.run_forever())
    try:
        await until(lambda:claim.await_count==1)
        scheduler.ready.clear(); scheduler.wake.set()
        await until(lambda:claim.await_count>=3)
    finally:
        await stop(task)


@pytest.mark.parametrize('value',[0,-1,'bad',float('nan'),float('inf')])
def test_invalid_reconcile_interval_rejected(value):
    with pytest.raises(service_settings.ConfigurationError):
        service_settings.load_settings(config={'AGENT_WORKER_RECONCILE_INTERVAL_SECONDS':value},environ={})


def test_signal_config_precedence_and_listener_budget():
    s=service_settings.load_settings(config={'AGENT_WORKER_RECONCILE_INTERVAL_SECONDS':8},
        environ={'AGENT_WORKER_RECONCILE_INTERVAL_SECONDS':'2','AGENT_WORKER_NOTIFY_ENABLED':'false'})
    assert s.api.agent_worker_reconcile_interval_seconds==8
    assert s.api.agent_worker_notify_enabled is False
    assert s.summary()['connection_pool_limits']['notification_listener']==1


@pytest.mark.asyncio
async def test_listener_references_are_independent_and_close_observed():
    entered, release = asyncio.Event(), asyncio.Event()
    callbacks={}; closed=[]
    class Connection:
        def add_termination_listener(self, callback):pass
        async def add_listener(self, channel, callback):callbacks[channel]=callback
        async def close(self, **_):
            entered.set(); await release.wait(); closed.append(True)
        def terminate(self):pass
    connect=AsyncMock(return_value=Connection())
    signals=PostgresSignals('postgresql://u:p@localhost/db',connect=connect)
    worker_ref=signals.subscribe(COMMAND_CHANNEL,lambda _:None)
    stream_ref=signals.subscribe(RUN_CHANNEL,lambda _:None)
    await worker_ref.__aenter__()
    await asyncio.wait_for(signals.ready.wait(),1)
    await stream_ref.__aenter__()
    assert connect.await_count==1 and len(signals.subscriptions)==2
    await stream_ref.__aexit__(None,None,None)
    assert signals.ready.is_set() and not closed
    cleanup=asyncio.create_task(worker_ref.__aexit__(None,None,None))
    await entered.wait(); cleanup.cancel(); await asyncio.sleep(.01); cleanup.cancel()
    assert not cleanup.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):await cleanup
    assert closed==[True] and not signals.subscriptions and signals.task is None


@pytest.mark.asyncio
async def test_bad_subscriber_does_not_break_delivery_or_leak_reference():
    connection=SimpleNamespace(add_termination_listener=lambda _:None,
        add_listener=AsyncMock(),close=AsyncMock(),terminate=lambda:None)
    signals=PostgresSignals('postgresql://u:p@localhost/db',connect=AsyncMock(return_value=connection))
    delivered=[]
    def failed(_):raise ValueError('subscriber failed')
    async with signals.subscribe(COMMAND_CHANNEL,delivered.append):
        await asyncio.wait_for(signals.ready.wait(),1)
        async with signals.subscribe(COMMAND_CHANNEL,failed):
            delivered.clear()
            signals._emit(COMMAND_CHANNEL,'fixture')
            assert delivered==['fixture'] and len(signals.subscriptions)==2
    assert not signals.subscriptions and signals.task is None
    connection.close.assert_awaited_once()
