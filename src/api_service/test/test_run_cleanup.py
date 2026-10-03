"""Execution cleanup races, using real asyncio/SQLAlchemy queue scheduling."""
from api_service.runs.errors import CancellationRequested
from api_service.runs.monitoring import run_cancellable
import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastapi import APIRouter, FastAPI
from sqlalchemy.util.concurrency import greenlet_spawn
from sqlalchemy.util.queue import AsyncAdaptedQueue

import service_settings
import service_bootstrap
import api_service.runs.monitoring as runs
import api_service.services.task_service as tasks
from service_contracts.execution import ExecutionNeedsRecovery
from api_service.core.execution_lifecycle import execution_health
from api_service.services.llm_token_event_service import LLMTokenEventBuffer
from api_service.services.task_service import TaskService


@pytest_asyncio.fixture(autouse=True)
async def isolated(monkeypatch):
    monkeypatch.setattr(service_settings, '_snapshot', None)
    service_settings.configure(service_settings.load_settings(config={
        'AGENT_WORKER_ENABLED': False, 'TASK_RECONCILER_ENABLED': False,
        'EVENT_WORKER_ENABLED': False, 'RUN_CLEANUP_TIMEOUT_SECONDS': .05, 'SHUTDOWN_DRAIN_SECONDS': 0, 'SHUTDOWN_TIMEOUT_SECONDS': .02,
        'RUN_MONITOR_TIMEOUT_SECONDS': .1, 'TASK_LEASE_SECONDS': 3,
    }, environ={}))
    monkeypatch.setattr(execution_health, 'faults', {})
    monkeypatch.setattr(execution_health, 'recorders', set())
    monkeypatch.setattr(execution_health, '_record', AsyncMock())
    yield
    if execution_health.recorders:
        await asyncio.gather(*execution_health.recorders)


def fake_db(monkeypatch, scalar):
    @asynccontextmanager
    async def session():
        yield type('DB', (), {'scalar': staticmethod(scalar)})()
    monkeypatch.setattr(runs, 'get_session_factory', lambda: session)


@pytest.mark.asyncio
async def test_actual_watcher_pool_queue_race_twenty_times(monkeypatch):
    # Same connection queue race as the diagnostic, with the ACTUAL watcher.
    for _ in range(20):
        queue, entered = AsyncAdaptedQueue(), asyncio.Event()
        watchers = []

        async def query(_statement):
            watchers.append(asyncio.current_task())
            entered.set()
            return await greenlet_spawn(queue.get, True, 30)

        fake_db(monkeypatch, query)

        async def graph():
            await entered.wait()
            queue.put_nowait(None)
            return {'completed': True}

        assert await asyncio.wait_for(run_cancellable(uuid4(), graph()), .5) == {'completed': True}
        assert all(w.done() and w.cancelling() == 0 for w in watchers)
    assert execution_health.healthy


@pytest.mark.asyncio
async def test_graph_failure_preserved_and_monitor_stopped(monkeypatch):
    fake_db(monkeypatch, AsyncMock(return_value=None))
    async def graph():
        raise ValueError('graph failure')
    with pytest.raises(ValueError, match='graph failure'):
        await run_cancellable(uuid4(), graph())
    assert execution_health.healthy


@pytest.mark.asyncio
async def test_user_cancel_is_reported_only_after_graph_cleanup(monkeypatch):
    started, stopped = asyncio.Event(), asyncio.Event()
    async def query(_):
        await started.wait()
        return object()
    fake_db(monkeypatch, query)
    async def graph():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            stopped.set()
    with pytest.raises(CancellationRequested):
        await run_cancellable(uuid4(), graph())
    assert stopped.is_set() and execution_health.healthy


@pytest.mark.asyncio
async def test_monitor_error_is_not_misreported_as_user_cancel(monkeypatch):
    fake_db(monkeypatch, AsyncMock(side_effect=OSError('query failed')))
    stopped = asyncio.Event()
    async def graph():
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    with pytest.raises(ExecutionNeedsRecovery):
        await run_cancellable(uuid4(), graph())
    assert stopped.is_set() and not execution_health.healthy


@pytest.mark.asyncio
async def test_monitor_query_has_deadline(monkeypatch):
    async def query(_):
        await asyncio.Event().wait()
    fake_db(monkeypatch, query)
    with pytest.raises(ExecutionNeedsRecovery):
        await asyncio.wait_for(run_cancellable(uuid4(), asyncio.sleep(10)), .5)


@pytest.mark.asyncio
async def test_repeated_owner_cancel_waits_for_graph_finally(monkeypatch):
    fake_db(monkeypatch, AsyncMock(return_value=None))
    started, cleaning, release, stopped = [asyncio.Event() for _ in range(4)]
    async def graph():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
            stopped.set()
    owner = asyncio.create_task(run_cancellable(uuid4(), graph()))
    await started.wait()
    owner.cancel()
    await cleaning.wait()
    owner.cancel()
    await asyncio.sleep(0)
    assert not owner.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await owner
    assert stopped.is_set() and execution_health.healthy


@pytest.mark.asyncio
async def test_uncooperative_graph_quarantines_without_abandoning_owner(monkeypatch):
    fake_db(monkeypatch, AsyncMock(return_value=object()))
    release, started = asyncio.Event(), asyncio.Event()
    async def graph():
        started.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                pass
        return {}
    owner = asyncio.create_task(run_cancellable(uuid4(), graph()))
    try:
        await started.wait()
        await asyncio.sleep(.14)
        assert not execution_health.healthy
        assert not owner.done(), 'must not reuse resources while graph is still alive'
        from api_service.agent_run_worker import claim_one
        with pytest.raises(ExecutionNeedsRecovery):
            await claim_one()
    finally:
        release.set()
        with pytest.raises(ExecutionNeedsRecovery):
            await asyncio.wait_for(owner, .5)


@pytest.mark.asyncio
async def test_heartbeat_normal_stop_does_not_cancel_db_loop():
    async with TaskService.lease_heartbeat(uuid4(), uuid4(), run_id=uuid4()) as heartbeat:
        await asyncio.sleep(0)
    assert heartbeat.done() and heartbeat.cancelling() == 0
    assert execution_health.healthy


@pytest.mark.asyncio
async def test_lease_loss_stops_graph(monkeypatch):
    @asynccontextmanager
    async def session():
        yield object()
    monkeypatch.setattr(tasks, 'get_session_factory', lambda: session)
    monkeypatch.setattr(TaskService, 'heartbeat', AsyncMock(return_value=False))
    fake_db(monkeypatch, AsyncMock(return_value=None))
    run_id = uuid4()
    with pytest.raises(ExecutionNeedsRecovery):
        async with TaskService.lease_heartbeat(uuid4(), uuid4(), run_id=run_id) as heartbeat:
            await asyncio.wait_for(run_cancellable(run_id, asyncio.sleep(10), observers=(heartbeat,)), 2)
    assert not execution_health.healthy


@pytest.mark.asyncio
async def test_token_close_flushes_tail_before_return(monkeypatch):
    buffer = LLMTokenEventBuffer(task_id=uuid4(), run_id=uuid4())
    append = AsyncMock()
    monkeypatch.setattr(buffer, '_append', append)
    buffer.start()
    await buffer.on_llm_new_token('tail', run_id='llm')
    await buffer.close()
    await buffer.close()
    append.assert_awaited_once_with('llm', 'tail')
    assert buffer.consumer.done() and buffer.consumer.cancelling() == 0


@pytest.mark.asyncio
async def test_token_writer_failure_requires_recovery(monkeypatch):
    buffer = LLMTokenEventBuffer(task_id=uuid4(), run_id=uuid4())
    monkeypatch.setattr(buffer, '_append', AsyncMock(side_effect=OSError('write failed')))
    buffer.start()
    await buffer.on_llm_new_token('tail', run_id='llm')
    with pytest.raises(ExecutionNeedsRecovery):
        await buffer.close()
    assert not execution_health.healthy


@pytest.mark.asyncio
async def test_token_flush_deadline_is_not_success(monkeypatch):
    buffer = LLMTokenEventBuffer(task_id=uuid4(), run_id=uuid4())
    async def append(*_):
        await asyncio.Event().wait()
    monkeypatch.setattr(buffer, '_append', append)
    buffer.start()
    await buffer.on_llm_new_token('tail', run_id='llm')
    with pytest.raises(ExecutionNeedsRecovery):
        await asyncio.wait_for(buffer.close(), .5)
    assert buffer.consumer.done() and not execution_health.healthy


@pytest.mark.asyncio
async def test_health_endpoints_expose_quarantine():
    app = service_bootstrap.create_app(service_settings.get_settings())
    app.state.service_runtime.started = True
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.get('/service/live')).status_code == 200
        assert (await client.get('/service/ready')).status_code == 200
        execution_health.fail(uuid4(), 'test')
        assert (await client.get('/service/live')).status_code == 503
        assert (await client.get('/service/ready')).status_code == 503


@pytest.mark.asyncio
async def test_shutdown_timeout_does_not_close_resources_under_live_worker():
    release, started = asyncio.Event(), asyncio.Event()
    async def worker():
        started.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                pass
    closed = AsyncMock()
    settings = service_settings.get_settings()
    app = service_bootstrap.attach_service(FastAPI(), settings, router=APIRouter(),
        background_factories={'worker': worker}, close_resources=closed)
    try:
        with pytest.raises(RuntimeError, match='shutdown deadline'):
            async with app.router.lifespan_context(app):
                await started.wait()
        closed.assert_not_awaited()
    finally:
        release.set()
        await asyncio.gather(*app.state.service_runtime.tasks.values())


@pytest.mark.parametrize('key,value', [
    ('RUN_CLEANUP_TIMEOUT_SECONDS', 0), ('RUN_CLEANUP_TIMEOUT_SECONDS', float('inf')),
    ('RUN_MONITOR_TIMEOUT_SECONDS', -1), ('RUN_MONITOR_TIMEOUT_SECONDS', float('nan')),
])
def test_invalid_cleanup_deadlines_rejected(key, value):
    with pytest.raises(service_settings.ConfigurationError):
        service_settings.load_settings(config={key: value}, environ={})
