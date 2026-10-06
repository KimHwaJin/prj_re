"""Diagnostic hooks must preserve outcomes and isolate concurrent Run contexts."""
import asyncio
import json
from pathlib import Path

import pytest

from dtest.infrastructure.observability.diagnostics import graph_callbacks, instrument_async_methods, run_trace, span
import dtest.settings.loader as service_settings
@pytest.fixture(autouse=True)
def isolated_config(monkeypatch):
    # These tests exercise diagnostics settings, independent of developer YAML.
    # Production YAML correctly takes precedence over the environment.
    original = service_settings.load_settings
    monkeypatch.setattr(service_settings, "load_settings", lambda **kwargs: original(config={}, **kwargs))
    monkeypatch.setattr(service_settings, "_snapshot", None)


def rows(folder):
    return [json.loads(line) for path in folder.glob('runs-*.jsonl') for line in path.read_text().splitlines()]


@pytest.mark.asyncio
async def test_disabled_diagnostics_do_not_instrument(monkeypatch, tmp_path):
    monkeypatch.delenv('RUN_DIAGNOSTICS_DIR', raising=False)
    callbacks = [object()]
    assert graph_callbacks(callbacks) is callbacks
    async with run_trace('off', 'session') as trace:
        assert trace is None
        with span('no_op'):
            await asyncio.sleep(0)
    assert not rows(tmp_path)


@pytest.mark.asyncio
async def test_concurrent_traces_preserve_results_and_errors(monkeypatch, tmp_path):
    monkeypatch.setenv('RUN_DIAGNOSTICS_DIR', str(tmp_path))

    class Boundary:
        async def execute(self, value):
            await asyncio.sleep(.01)
            if value < 0:
                raise ValueError('secret input must not appear in trace')
            return value * 2

    async def one(run, value):
        async with run_trace(run, 'session'):
            target = instrument_async_methods(Boundary(), 'boundary', ('execute',))
            return await target.execute(value)

    values = await asyncio.gather(one('ok', 3), one('failed', -1), return_exceptions=True)
    assert values[0] == 6 and isinstance(values[1], ValueError)
    summaries = {r['run_id']: r for r in rows(tmp_path) if r['event'] == 'run_end'}
    assert summaries['ok']['timings']['boundary.execute']['outcomes'] == {'ok': 1}
    assert summaries['failed']['timings']['boundary.execute']['outcomes'] == {'ValueError': 1}
    assert 'secret input' not in json.dumps(rows(tmp_path))


@pytest.mark.asyncio
async def test_watchdog_captures_pending_await_and_cancellation(monkeypatch, tmp_path):
    monkeypatch.setenv('RUN_DIAGNOSTICS_DIR', str(tmp_path))
    monkeypatch.setenv('RUN_DIAGNOSTICS_STALL_SECONDS', '1')

    async def stuck():
        async with run_trace('stuck', 'session'):
            with span('intentional_wait'):
                await asyncio.Event().wait()

    task = asyncio.create_task(stuck())
    await asyncio.sleep(1.15)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    saved = rows(tmp_path)
    snapshot = next(r for r in saved if r['event'] == 'stall_snapshot')
    assert any(s['name'] == 'intentional_wait' for s in snapshot['active'])
    owner = next(t for t in snapshot['tasks'] if t['owner'])
    assert any(f['function'] == 'wait' for f in owner['stack'])
    assert saved[-1]['outcome'] == 'CancelledError'


@pytest.mark.asyncio
async def test_graph_callbacks_do_not_change_interrupt_or_resume(monkeypatch, tmp_path):
    from langgraph.graph import StateGraph, START, END
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import interrupt, Command
    from typing_extensions import TypedDict

    monkeypatch.setenv('RUN_DIAGNOSTICS_DIR', str(tmp_path))

    class State(TypedDict):
        result: str

    def approval(state):
        return {'result': interrupt('secret approval input')}

    graph = StateGraph(State).add_node('approval', approval).add_edge(START, 'approval').add_edge('approval', END).compile(checkpointer=InMemorySaver())
    for run, value in [('first', {'result': ''}), ('resume', Command(resume='approved'))]:
        async with run_trace(run, 'session'):
            result = await graph.ainvoke(value, {'configurable': {'thread_id': 'session'}, 'callbacks': graph_callbacks(None)})
        if run == 'first':
            assert result['__interrupt__'][0].value == 'secret approval input'
        else:
            assert result['result'] == 'approved'
    ends = [r for r in rows(tmp_path) if r['event'] == 'run_end']
    assert all('chain.approval' in r['timings'] for r in ends)
    assert 'secret approval input' not in json.dumps(rows(tmp_path))
