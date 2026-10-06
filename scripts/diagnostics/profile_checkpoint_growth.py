"""Opt-in local PG checkpoint growth probe; never a production load runner.

Uses actual registered Python fixture functions and production graph/receipts,
with zero-delay deterministic model roles and an in-process Executor double.
No HTTP, Redis worker, real model, Jupyter, or week-long timing is represented.
Every trial owns a fresh thread and root. SQL captures happen outside graph time.
"""
import argparse
import asyncio
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import replace
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import time
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts/benchmarks/worker_e2e'))

CASES = [
    {'name': f'output_{size}', 'steps': 1, 'batch': 1, 'log_bytes': size, 'repairs': 0}
    for size in (0, 65536, 1048576, 16777216)
] + [
    {'name': f'operations_{20 // batch}', 'steps': 20, 'batch': batch, 'log_bytes': 0, 'repairs': 0}
    for batch in (20, 4, 1)
] + [
    {'name': f'large_operations_{20 // batch}', 'steps': 20, 'batch': batch, 'log_bytes': 65536, 'repairs': 0}
    for batch in (20, 1)
] + [
    {'name': f'repair_{count}', 'steps': 3, 'batch': 3, 'log_bytes': 0, 'repairs': count}
    for count in (0, 1, 5, 10)
]


def aggregate(stored):
    """Logical payload, not heap, indexes, WAL, Python heap, or pending reads."""
    checkpoints, blobs, writes = (stored[k] for k in ('checkpoints', 'blobs', 'writes'))
    assert checkpoints and len({r['checkpoint_id'] for r in checkpoints}) == len(checkpoints)
    assert len({(r['channel'], r['version']) for r in blobs}) == len(blobs)
    assert len({(r['checkpoint_id'], r['task_id'], r['idx']) for r in writes}) == len(writes)
    latest = checkpoints[-1]
    versions = latest['versions']
    referenced = [r for r in blobs if versions.get(r['channel']) == r['version']]
    cp_bytes = sum(r['checkpoint_json_bytes'] + r['metadata_json_bytes'] for r in checkpoints)
    blob_bytes, write_bytes = sum(r['bytes'] for r in blobs), sum(r['bytes'] for r in writes)
    by_channel = defaultdict(lambda: {'blob_bytes': 0, 'write_bytes': 0})
    for r in blobs: by_channel[r['channel']]['blob_bytes'] += r['bytes']
    for r in writes: by_channel[r['channel']]['write_bytes'] += r['bytes']
    metadata = Counter()
    for r in checkpoints: metadata.update(r['component_json_bytes'])
    return {'checkpoint_count': len(checkpoints), 'blob_count': len(blobs), 'write_count': len(writes),
        'checkpoint_json_and_metadata_bytes': cp_bytes, 'blob_bytes': blob_bytes, 'write_bytes': write_bytes,
        'logical_payload_bytes': cp_bytes + blob_bytes + write_bytes,
        'latest_json_and_referenced_blobs_bytes': latest['checkpoint_json_bytes'] + latest['metadata_json_bytes'] + sum(r['bytes'] for r in referenced),
        'checkpoint_components': dict(metadata), 'channel_payloads': dict(by_channel)}


async def run_case(case, trial, directory, saver, metrics, dsn):
    import pytest
    from checkpoint_profile import capture
    from tests.agent_service import test_agentic_repair as fixture
    from dtest.agent_service.agents.analysis.agent_builders.conversation.agent import reply_schema
    from dtest.contracts.events import EventContext, ExecutorEvent
    from dtest.application.runs.graph_invocation import GraphInvocation
    started_calls = len(metrics['checkpoint_calls'])
    output_reads = []
    original = fixture.make_runtime

    async def make_runtime(settings, executor, **options):
        settings = replace(settings, agent_max_repair_attempts=10)  # Explicit fixture ceiling; deployment config is untouched.
        runtime, calls = await original(settings, executor, level=1, attempts=case['repairs'])
        definition = fixture.document(1, case['repairs'])
        if case['name'].startswith('repair_'):
            if case['repairs'] == 0:
                definition['steps'][1]['arguments']['divisor']['value'] = 2
            base_role = runtime.execution_role
            async def role(name, state, payload):
                if name == 'repair':
                    calls.append(deepcopy(payload))
                    # Distinct valid changes: unsuccessful attempts, then a valid final fix.
                    return fixture.correction(1, payload, value=2 if len(calls) == case['repairs'] else -len(calls))
                return await base_role(name, state, payload)
            runtime.execution_role = role
        else:
            code = "def growth_step(data=None, log_bytes=0):\n    globals()['growth_calls'] = globals().get('growth_calls', 0) + 1\n    if log_bytes:\n        print('L' * log_bytes)\n    return {'count': (data['count'] if data else 0) + 1}\n"
            runtime.catalog.sources['growth_step'], runtime.catalog.metadata['tools']['growth_step'] = fixture.source_info(code)
            runtime.catalog.metadata['skills']['repair_demo']['tools'].append('growth_step')
            runtime.catalog.revision = hashlib.sha256(fixture.canonical({'tools': runtime.catalog.sources, 'skills': runtime.catalog.skill_sources}).encode()).hexdigest()
            steps = []
            for index in range(case['steps']):
                previous = f's{index-1}'
                steps.append({'id': f's{index}', 'skill_id': 'repair_demo', 'tool_id': 'growth_step',
                    'description': 'Increment test object', 'depends_on': [previous] if index else [],
                    'arguments': {'data': {'source': 'step_output', 'step_id': previous, 'selector': []} if index else {'source': 'literal', 'value': None},
                                  'log_bytes': {'source': 'literal', 'value': case['log_bytes']}}})
            definition['steps'] = steps
            definition['execution'].update(repair_level=0, max_repair_attempts=0)
            if case['batch'] < case['steps']:
                definition['execution'].update(review_mode='every_n_tools', review_interval_tools=case['batch'])
            definition['expected_outputs'][0]['source'] = {'source': 'step_output', 'step_id': steps[-1]['id'], 'selector': ['count']}
        async def respond(*args):
            return reply_schema(runtime.catalog, 1, repair_attempts=10)(kind='plans', message='Storage growth fixture', plans=[{'definition': definition}])
        runtime.respond = respond
        return runtime, calls

    graph_ms = 0
    async def timed(call):
        nonlocal graph_ms
        began = time.perf_counter()
        result = await call
        graph_ms += (time.perf_counter() - began) * 1000
        return result

    with pytest.MonkeyPatch.context() as patch:
        from dtest.agent_service.agents.analysis.execution import nodes
        original_reader = nodes.read_operation_observations
        def measured_reader(*args):
            began = time.perf_counter()
            result = original_reader(*args)
            output_reads.append({'ms': (time.perf_counter() - began) * 1000, 'steps': len(result)})
            return result
        patch.setattr(nodes, 'read_operation_observations', measured_reader)
        patch.setattr(fixture, 'make_runtime', make_runtime)
        patch.setattr(fixture, 'InMemorySaver', lambda: saver)
        runtime, executor, graph, config, state, calls, _, _ = await timed(fixture.scenario(directory, patch))
        approval = deepcopy(state['approved_snapshot'])
        event_index = 0
        while event_index < len(executor.events):
            event = executor.events[event_index]
            context = EventContext(namespace='growth-fixture', session_id=config['configurable']['thread_id'],
                task_id=state['task_id'], execution_id=UUID(executor.id), command_id=uuid4(), event=ExecutorEvent.model_validate(event))
            async def deliver():
                await GraphInvocation(graph, model_validator=None).executor_resume(context)
                return (await graph.aget_state(config)).values
            state = await timed(deliver())
            assert state['approved_snapshot'] == approval
            event_index += 1
        assert executor.calls[-1][0].endswith('/finalize')
        before = len(executor.calls)
        # Same receipt at the durable terminal wait must not submit/finalize again.
        await timed(GraphInvocation(graph, model_validator=None).executor_resume(context))
        assert len(executor.calls) == before
        raw_tuple = await saver.aget_tuple(config)
        restore = []
        for _ in range(5):
            began = time.perf_counter()
            restored = await saver.aget_tuple(config)
            restore.append((time.perf_counter() - began) * 1000)
            assert restored.checkpoint == raw_tuple.checkpoint and restored.pending_writes == raw_tuple.pending_writes
        terminal = executor.event('execution.completed', {'status': 'SUCCEEDED', 'error': None})
        terminal_context = EventContext(namespace='growth-fixture', session_id=config['configurable']['thread_id'],
            task_id=state['task_id'], execution_id=UUID(executor.id), command_id=uuid4(), event=ExecutorEvent.model_validate(terminal))
        async def finish():
            await GraphInvocation(graph, model_validator=None).executor_resume(terminal_context)
            return (await graph.aget_state(config)).values
        state = await timed(finish())
        assert state['final_response']['status'] == 'analysis_completed'
        assert state['repair_attempts'] == case['repairs']
        assert len(calls) == case['repairs']
        assert not (await graph.aget_state(config)).next
        if case['name'].startswith('repair_'):
            assert executor.globals['repair_load_calls'] == 1
            assert state['observations'][-1]['summary']['items']['sum'] == 6
        else:
            assert executor.globals['growth_calls'] == case['steps']
            assert state['observations'][-1]['summary']['items']['count'] == case['steps']
        assert all(sum(len(t) for t in o['text']) <= runtime.settings.agent_observation_max_chars for o in state['observations'])
        assert all(o['summary'] for o in state['observations'] if o['status'] == 'SUCCEEDED')
        after_finish = len(executor.calls)
        await timed(GraphInvocation(graph, model_validator=None).executor_resume(terminal_context))
        assert len(executor.calls) == after_finish
        measured = metrics['checkpoint_calls'][started_calls:]
        assert not any(row['error'] for row in measured)
        assert saver.conn.get_stats()['pool_available'] == saver.conn.get_stats()['pool_size']
        stored = await asyncio.to_thread(capture, dsn, [config['configurable']['thread_id']])
        stats = aggregate(stored)
        assert sum(r['method'] == 'aput' for r in measured) == stats['checkpoint_count']
        return {'case': case, 'trial': trial, 'thread_id': config['configurable']['thread_id'],
            'graph_and_double_ms': graph_ms, 'wait_restore_ms': restore, 'output_read_calls': output_reads,
            'operation_count': executor.number, 'executor_calls': len(executor.calls),
            'raw_stdout_bytes': sum(p.stat().st_size for p in directory.rglob('stdout.txt')),
            'observation_text_chars': sum(len(t) for o in state['observations'] for t in o['text']),
            'observation_count': len(state['observations']), 'repair_history_count': len(state['repair_history']),
            'state_outcome': {'status': state['final_response']['status'], 'completed_steps': state['completed_steps'],
                'repair_attempts': state['repair_attempts'], 'approval_preserved': state['approved_snapshot'] == approval,
                'duplicate_event_no_extra_delivery': True, 'summary_preserved': True, 'pool_returned': True},
            'storage': stats, 'stored_rows': stored, 'checkpoint_calls': measured}


async def main_async(args, dsn):
    from checkpoint_profile import install
    from dtest.agent_service.runtime.langgraph.checkpointer import create_checkpointer
    metrics = {'checkpoint_calls': []}
    install(metrics, lambda: True)
    rows = []
    order = [(case, trial) for trial in range(1, args.repeats + 1) for case in CASES]
    random.Random(6504).shuffle(order)
    async with create_checkpointer(dsn, setup_on_start=True, min_size=1, max_size=2) as saver:
        for case, trial in order:
            directory = args.fixture_dir / f"{case['name']}-{trial}"
            directory.mkdir()
            row = await run_case(case, trial, directory, saver, metrics, dsn)
            rows.append(row)
            print(json.dumps({'case': case['name'], 'trial': trial, 'ms': round(row['graph_and_double_ms'], 1),
                              'KiB': round(row['storage']['logical_payload_bytes']/1024, 1)}, ensure_ascii=False), flush=True)
    # Reopen the pool and decode all completed rows; this is NOT a process-kill recovery test.
    # No cleanup of another application's threads.
    async with create_checkpointer(dsn, setup_on_start=False, min_size=1, max_size=2) as saver:
        for row in rows:
            item = await saver.aget_tuple({'configurable': {'thread_id': row['thread_id']}})
            assert item.checkpoint['channel_values']['final_response']['status'] == 'analysis_completed'
        assert saver.conn.get_stats()['pool_available'] == saver.conn.get_stats()['pool_size']
    source = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    paths = ['src/dtest.agent_service/agents/analysis/planning/graph.py', 'src/dtest.agent_service/agents/analysis/execution/nodes.py',
             'src/dtest/infrastructure/executor/observations.py', 'src/dtest.agent_service/runtime/langgraph/pooled_saver.py',
             'scripts/diagnostics/profile_checkpoint_growth.py']
    return {'scope': 'sequential local PG functional/storage microprobe; deterministic zero-delay model roles; Python Executor double; no HTTP/worker/real model throughput claim',
        'seed': 6504, 'repeats': args.repeats, 'source_commit': source,
        'runtime_and_harness_sha256': {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths},
        'versions': {p: importlib.metadata.version(p) for p in ('langgraph', 'langgraph-checkpoint', 'langgraph-checkpoint-postgres', 'psycopg', 'psycopg-pool')},
        'all_completed_rows_read_after_pool_reopen': True, 'trials': rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=3, choices=range(1, 6))
    args = parser.parse_args()
    from psycopg.conninfo import conninfo_to_dict
    dsn = os.environ['DTEST_GROWTH_PROFILE_DSN']
    info = conninfo_to_dict(dsn)
    if info.get('host') not in {'localhost', '127.0.0.1'} or info.get('dbname') != 'agentic_checkpoint_test':
        raise ValueError('Only a disposable local agentic_checkpoint_test DB is permitted')
    if args.output.exists() or args.fixture_dir.exists():
        raise ValueError('Use fresh output and fixture directory paths')
    args.fixture_dir.mkdir(parents=True)
    result = asyncio.run(main_async(args, dsn))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
