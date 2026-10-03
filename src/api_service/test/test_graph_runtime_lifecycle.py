"""Graph resource ownership, independent of external model/DB/Redis services."""
import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
import service_settings
from api_service.services.agent_graph_service import AgentGraphRuntime, GraphResourcesBusy


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    monkeypatch.setattr(service_settings, '_snapshot', None)
    service_settings.configure(service_settings.load_settings(config={
        'AGENT_WORKER_ENABLED': False, 'TASK_RECONCILER_ENABLED': False,
        'EVENT_WORKER_ENABLED': False, 'SHUTDOWN_TIMEOUT_SECONDS': .2,
    }, environ={}))


def counted_runtime(monkeypatch):
    runtime = AgentGraphRuntime()
    counters = {'opened': 0, 'closed': 0}
    graph = object()
    @asynccontextmanager
    async def context():
        counters['opened'] += 1
        try:
            yield graph
        finally:
            counters['closed'] += 1
    monkeypatch.setattr(runtime, '_graph_context', context)
    return runtime, counters, graph


@pytest.mark.asyncio
async def test_sequential_borrows_build_once_and_close_at_shutdown(monkeypatch):
    runtime, count, graph = counted_runtime(monkeypatch)
    for _ in range(20):
        async with runtime.open_graph() as value:
            assert value is graph and count == {'opened': 1, 'closed': 0}
    await runtime.shutdown()
    await runtime.shutdown()
    assert count == {'opened': 1, 'closed': 1}


@pytest.mark.asyncio
async def test_concurrent_borrows_do_not_replace_another_callers_resources(monkeypatch):
    runtime, count, graph = counted_runtime(monkeypatch)
    release, all_entered = asyncio.Event(), asyncio.Event()
    entered = 0
    async def invoke():
        nonlocal entered
        async with runtime.open_graph() as value:
            entered += 1
            if entered == 20:
                all_entered.set()
            await release.wait()
            assert value is graph and count['closed'] == 0
    owners = [asyncio.create_task(invoke()) for _ in range(20)]
    try:
        await asyncio.wait_for(all_entered.wait(), 1)
        assert count['opened'] == 1
    finally:
        release.set()
        await asyncio.gather(*owners)
        await runtime.shutdown()
    assert count['closed'] == 1


@pytest.mark.asyncio
async def test_failed_initialization_closes_partial_resources_and_can_retry(monkeypatch):
    runtime = AgentGraphRuntime()
    count = {'attempt': 0, 'close': 0}
    @asynccontextmanager
    async def context():
        count['attempt'] += 1
        try:
            if count['attempt'] == 1:
                raise OSError('init failed')
            yield 'graph'
        finally:
            count['close'] += 1
    monkeypatch.setattr(runtime, '_graph_context', context)
    with pytest.raises(OSError):
        async with runtime.open_graph():
            pytest.fail('unreachable')
    assert count == {'attempt': 1, 'close': 1}
    async with runtime.open_graph() as graph:
        assert graph == 'graph'
    await runtime.shutdown()
    assert count == {'attempt': 2, 'close': 2}


@pytest.mark.asyncio
async def test_canceled_initializer_keeps_successful_resource_owned_until_shutdown(monkeypatch):
    runtime = AgentGraphRuntime()
    entered, release, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    @asynccontextmanager
    async def context():
        entered.set()
        await release.wait()
        try:
            yield 'graph'
        finally:
            closed.set()
    monkeypatch.setattr(runtime, '_graph_context', context)
    async def invoke():
        async with runtime.open_graph():
            pytest.fail('canceled borrower must not enter')
    caller = asyncio.create_task(invoke())
    await entered.wait()
    caller.cancel()
    await asyncio.sleep(0)
    caller.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert not closed.is_set()
    async with runtime.open_graph() as graph:
        assert graph == 'graph'
    await runtime.shutdown()
    assert closed.is_set()


@pytest.mark.asyncio
async def test_shutdown_drains_existing_borrow_and_rejects_new_ones(monkeypatch):
    runtime, count, _ = counted_runtime(monkeypatch)
    borrow = runtime.open_graph()
    await borrow.__aenter__()
    shutdown = asyncio.create_task(runtime.shutdown())
    await asyncio.sleep(0)
    assert not shutdown.done() and count['closed'] == 0
    async with asyncio.timeout(.05):
        with pytest.raises(GraphResourcesBusy):
            async with runtime.open_graph():
                pass
    await borrow.__aexit__(None, None, None)
    await shutdown
    with pytest.raises(GraphResourcesBusy):
        async with runtime.open_graph():
            pass
    assert count['closed'] == 1


@pytest.mark.asyncio
async def test_shutdown_timeout_retains_resources_and_allows_later_close(monkeypatch):
    runtime, count, _ = counted_runtime(monkeypatch)
    borrow = runtime.open_graph()
    await borrow.__aenter__()
    with pytest.raises(GraphResourcesBusy, match='deadline'):
        await runtime.shutdown(timeout=.01)
    assert count['closed'] == 0
    with pytest.raises(GraphResourcesBusy):
        async with runtime.open_graph():
            pass
    await borrow.__aexit__(None, None, None)
    await runtime.shutdown()
    assert count['closed'] == 1


@pytest.mark.asyncio
async def test_borrower_exception_or_cancel_does_not_dispose_shared_graph(monkeypatch):
    runtime, count, _ = counted_runtime(monkeypatch)
    for error in (ValueError('graph error'), asyncio.CancelledError()):
        with pytest.raises(type(error)):
            async with runtime.open_graph():
                raise error
        assert count == {'opened': 1, 'closed': 0}
    await runtime.shutdown()
    assert count['closed'] == 1


@pytest.mark.asyncio
async def test_cannot_reset_or_override_while_borrowed(monkeypatch):
    runtime, _, _ = counted_runtime(monkeypatch)
    async with runtime.open_graph():
        with pytest.raises(GraphResourcesBusy):
            runtime.start()
        with pytest.raises(RuntimeError):
            runtime.override_graph(object())
    await runtime.shutdown()
    runtime.start()
    runtime.override_graph('test graph')
    async with runtime.open_graph() as graph:
        assert graph == 'test graph'
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_event_loop_sharing_is_rejected(monkeypatch):
    runtime, _, _ = counted_runtime(monkeypatch)
    async with runtime.open_graph():
        pass
    async def other_loop():
        with pytest.raises(RuntimeError, match='event loops'):
            async with runtime.open_graph():
                pass
    await asyncio.to_thread(lambda: asyncio.run(other_loop()))
    await runtime.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [GraphResourcesBusy('live borrower'), asyncio.CancelledError()])
async def test_service_does_not_close_other_pools_while_graph_still_owned(monkeypatch, error):
    from service_bootstrap import _close_resources
    import api_service.core.database as database
    import api_service.agent_worker.api_bridge as bridge
    from api_service.services.agent_graph_service import runtime
    monkeypatch.setattr(runtime, 'shutdown', AsyncMock(side_effect=error))
    close_database, close_bridge = AsyncMock(), AsyncMock()
    monkeypatch.setattr(database, 'close_database', close_database)
    monkeypatch.setattr(bridge, 'close_api_worker_bridge', close_bridge)
    with pytest.raises(type(error)):
        await _close_resources()
    close_database.assert_not_awaited()
    close_bridge.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('fail', [False, True])
async def test_event_ingress_never_builds_or_invokes_graph(monkeypatch, fail):
    import api_service.agent_worker.worker_main as entry
    import api_service.services.agent_graph_service as shared
    opened = AsyncMock(side_effect=AssertionError('Ingress borrowed a graph'))
    monkeypatch.setattr(shared.runtime, 'open_graph', opened)
    calls = []
    class Worker:
        def __init__(self, settings, event_types):
            assert event_types == {'execution.operation_completed', 'execution.completed'}
        async def __aenter__(self):
            calls.append('open')
            return self
        async def __aexit__(self, *_):
            calls.append('close')
        def add_readiness_check(self, *args):
            pass
        async def run(self, *, stop_event=None):
            calls.append('run')
            if fail:
                raise RuntimeError('worker failure')
    monkeypatch.setattr(entry, 'ExecutorWorker', Worker)
    if fail:
        with pytest.raises(RuntimeError, match='worker failure'):
            await entry.main(install_signals=False)
    else:
        await entry.main(install_signals=False)
    assert calls == ['open', 'run', 'close']
    opened.assert_not_called()


@pytest.mark.asyncio
async def test_common_event_command_borrows_shared_graph(monkeypatch):
    import api_service.agent_run_worker as worker
    import api_service.services.agent_graph_service as shared
    import api_service.runs.graph_invocation as boundary
    from types import SimpleNamespace
    runtime = AgentGraphRuntime()
    graph = object()
    runtime.override_graph(graph)
    monkeypatch.setattr(shared, 'runtime', runtime)
    applied = AsyncMock()
    class Invocation:
        def __init__(self, borrowed, **kwargs):
            assert borrowed is graph and runtime._active == 1
        async def executor_event(self, context):
            await applied(context)
    monkeypatch.setattr(boundary, 'GraphInvocation', Invocation)
    context = SimpleNamespace()
    await worker.execute_event(context)
    applied.assert_awaited_once_with(context)
    assert runtime._active == 0
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_shared_pool_visible_in_every_run_trace_without_double_wrapping(monkeypatch, tmp_path):
    from dataclasses import replace
    from service_runtime.diagnostics import observe_pool, run_trace
    configured = service_settings.get_settings()
    monkeypatch.setattr(service_settings, '_snapshot', replace(configured, diagnostics_dir=tmp_path))
    class Pool:
        async def open(self): pass
        async def close(self): pass
        async def getconn(self): return 'connection'
        async def putconn(self, conn): pass
        def get_stats(self): return {'pool_size': 1}
    runtime = AgentGraphRuntime()
    pool = Pool()
    @asynccontextmanager
    async def context():
        observe_pool(pool, 'checkpoint_pool')
        runtime._observed_pools = ((pool, 'checkpoint_pool'),)
        yield pool
    monkeypatch.setattr(runtime, '_graph_context', context)
    try:
        for index in range(2):
            async with run_trace(f'run-{index}', f'session-{index}') as trace:
                async with runtime.open_graph() as shared:
                    await shared.getconn()
                assert trace.pools['checkpoint_pool']() == {'pool_size': 1}
                assert trace.totals['checkpoint_pool.getconn']['count'] == 1
    finally:
        await runtime.shutdown()
