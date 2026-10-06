"""Cooperative drain, signal handoff and Redis consumer cancellation boundaries."""
import asyncio
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import os
import signal
import subprocess
import sys
import time

import pytest

from service_bootstrap import BackgroundRuntime
from service_settings import load_settings, ConfigurationError
from api_service.core.execution_lifecycle import execution_health
from api_service.worker.consumer import RedisStreamConsumer, RedisStreamConsumerConfig, HandlerResult, AckDecision
from api_service.worker.runtime import ExecutorWorker


@pytest.fixture(autouse=True)
def healthy(monkeypatch):
    monkeypatch.setattr(execution_health,'faults',{})
    monkeypatch.setattr(execution_health,'recorders',set())


@pytest.mark.asyncio
async def test_stop_is_cooperative_and_repeated_stop_keeps_deadlines():
    stop,entered,release=asyncio.Event(),asyncio.Event(),asyncio.Event()
    canceled=[]
    async def work():
        entered.set()
        try:
            await stop.wait(); await release.wait()
        except asyncio.CancelledError:
            canceled.append(True); raise
    runtime=BackgroundRuntime({'worker':work},1,stop_event=stop,drain_timeout=1)
    await runtime.start(); await entered.wait()
    runtime.request_stop(); deadline=runtime._drain_deadline
    runtime.request_stop()
    assert not runtime.ready and deadline==runtime._drain_deadline
    assert not runtime.tasks['worker'].done()
    release.set(); await runtime.stop(); await runtime.stop()
    assert not canceled and not runtime.tasks


@pytest.mark.asyncio
async def test_repeated_cancellation_of_stop_caller_does_not_abandon_owned_work():
    entered,release=asyncio.Event(),asyncio.Event()
    async def work():
        entered.set(); await release.wait()
    runtime=BackgroundRuntime({'worker':work},1,drain_timeout=1)
    await runtime.start(); await entered.wait()
    task=asyncio.create_task(runtime.stop())
    await asyncio.sleep(.01); task.cancel(); await asyncio.sleep(.01); task.cancel()
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not runtime.tasks


@pytest.mark.asyncio
async def test_deadline_cancels_only_after_grace():
    canceled=asyncio.Event()
    async def work():
        try:
            await asyncio.Event().wait()
        finally:
            canceled.set()
    runtime=BackgroundRuntime({'worker':work},1,drain_timeout=.12)
    await runtime.start(); runtime.request_stop()
    await asyncio.sleep(.02)
    assert not canceled.is_set()
    await runtime.stop()
    assert canceled.is_set()


@pytest.mark.parametrize('value',[-1,'bad',float('nan'),float('inf')])
def test_invalid_drain_settings_rejected(value):
    with pytest.raises(ConfigurationError):
        load_settings(config={'SHUTDOWN_DRAIN_SECONDS':value},environ={})


def test_drain_settings_precedence_and_zero():
    assert load_settings(config={},environ={}).shutdown_drain_seconds==20
    assert load_settings(config={},environ={'SHUTDOWN_DRAIN_SECONDS':'3'}).shutdown_drain_seconds==3
    settings=load_settings(config={'SHUTDOWN_DRAIN_SECONDS': 0},
                           environ={'SHUTDOWN_DRAIN_SECONDS':'3'})
    assert settings.summary()['shutdown_drain_seconds']==0


def consumer_for(monkeypatch,handle):
    redis=SimpleNamespace(xreadgroup=AsyncMock(return_value=[('stream',[('1-0',{})])]))
    handler=SimpleNamespace(lock_key=lambda _:None,handle=handle)
    consumer=RedisStreamConsumer(redis,RedisStreamConsumerConfig(
        stream='stream',group='group',consumer_prefix='drain',concurrency=1,
    ),lambda _:handler)
    monkeypatch.setattr(consumer,'initialize',AsyncMock())
    monkeypatch.setattr(consumer,'_claim_stale',AsyncMock(return_value=('0-0',[])))
    monkeypatch.setattr(consumer,'_ack',AsyncMock())
    return consumer,redis


@pytest.mark.asyncio
async def test_redis_stop_finishes_active_handler_and_ack_without_next_read(monkeypatch):
    entered,release=asyncio.Event(),asyncio.Event()
    async def handle(_):
        entered.set(); await release.wait(); return HandlerResult(AckDecision.ACK)
    consumer,redis=consumer_for(monkeypatch,handle)
    runner=asyncio.create_task(consumer.run())
    await entered.wait(); consumer.request_stop()
    draining=asyncio.create_task(consumer.shutdown(None))
    await asyncio.sleep(.01); assert not draining.done()
    release.set(); await draining; await runner
    consumer._ack.assert_awaited_once()
    assert redis.xreadgroup.await_count==1


@pytest.mark.asyncio
async def test_redis_forced_cancel_preserves_handler_cleanup_under_repeated_cancel(monkeypatch):
    entered,cleaning,release=asyncio.Event(),asyncio.Event(),asyncio.Event()
    async def handle(_):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set(); await release.wait()
    consumer,_=consumer_for(monkeypatch,handle)
    runner=asyncio.create_task(consumer.run())
    await entered.wait(); runner.cancel(); await cleaning.wait(); runner.cancel()
    await asyncio.sleep(.01); assert not runner.done()
    release.set(); await asyncio.gather(runner,return_exceptions=True)
    consumer._ack.assert_not_awaited()
    assert not consumer.is_running


@pytest.mark.asyncio
async def test_executor_runtime_uses_service_stop_without_early_handler_cancel(monkeypatch):
    entered,release,stopped=asyncio.Event(),asyncio.Event(),asyncio.Event()
    async def handle(_):
        entered.set(); await release.wait(); return HandlerResult(AckDecision.ACK)
    consumer,_=consumer_for(monkeypatch,handle)
    worker=ExecutorWorker.__new__(ExecutorWorker)
    worker._running=False; worker._stop=asyncio.Event()
    worker._router_wake=asyncio.Event()
    worker.settings=SimpleNamespace(health_port=0,shutdown_seconds=0,poll_seconds=.01,idle_poll_seconds=.02)
    worker.consumers=[consumer]
    worker.router=SimpleNamespace(once=AsyncMock(return_value=0))
    worker._metrics=AsyncMock(return_value=0)
    runner=asyncio.create_task(worker.run(stop_event=stopped))
    await entered.wait(); stopped.set(); await asyncio.sleep(.02)
    assert not runner.done() and worker._stop.is_set()
    release.set(); await runner
    consumer._ack.assert_awaited_once()
    assert not worker._running


@pytest.mark.parametrize('repeat',[False,True])
def test_root_server_real_sigterm_drains_before_resource_close(tmp_path,repeat):
    output=tmp_path/'events.txt'
    child=tmp_path/'server.py'
    child.write_text('''import asyncio
from pathlib import Path
from fastapi import FastAPI,APIRouter
from service_bootstrap import attach_service,build_server
from service_settings import load_settings
p=Path(__import__('sys').argv[1])
def record(value):
    with p.open('a') as f: f.write(value+'\\n')
app=FastAPI()
async def worker():
    while not server.started: await asyncio.sleep(.01)
    record('started')
    await app.state.service_runtime.stop_event.wait()
    assert not app.state.service_runtime.ready
    record('draining')
    await asyncio.sleep(.25)
    record('finished')
async def close(): record('closed')
settings=load_settings(config={'AGENT_WORKER_ENABLED':False,
    'EVENT_WORKER_ENABLED':False,'TASK_RECONCILER_ENABLED':False,
    'SHUTDOWN_DRAIN_SECONDS':2,'SHUTDOWN_TIMEOUT_SECONDS':1},environ={})
attach_service(app,settings,router=APIRouter(),background_factories={'worker':worker},close_resources=close)
server=build_server(app,settings)
server.config.port=0
server.run()
''')
    root=Path(__file__).resolve().parents[3]
    env={**os.environ,'PYTHONPATH':str(root/'src'),'PYTHONDONTWRITEBYTECODE':'1'}
    with (tmp_path/'server.log').open('w') as log:
        process=subprocess.Popen([sys.executable,str(child),str(output)],cwd=tmp_path,env=env,stdout=log,stderr=log)
        def wait_text(text):
            end=time.monotonic()+10
            while time.monotonic()<end:
                if output.exists() and text in output.read_text():return
                assert process.poll() is None,(tmp_path/'server.log').read_text()
                time.sleep(.01)
            pytest.fail('server did not reach '+text)
        try:
            wait_text('started'); process.send_signal(signal.SIGTERM)
            if repeat:
                wait_text('draining'); process.send_signal(signal.SIGTERM)
            process.wait(timeout=10)
            assert output.read_text().splitlines()==['started','draining','finished','closed']
            assert process.returncode in (0,-signal.SIGTERM)
        finally:
            if process.poll() is None:
                process.kill(); process.wait()
