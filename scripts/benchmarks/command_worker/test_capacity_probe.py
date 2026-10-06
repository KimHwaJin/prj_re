"""Opt-in small actual-scheduler comparison; graph work is a fixed async delay.

Run this SAME file on an archived baseline and current source, in separate
processes, against disposable localhost identity_test PostgreSQL and Redis.
Starts from durable ready queues: admission/ingress setup, migrations, graph
construction/checkpoint/LLM, HTTP serving and shutdown are outside the timer.
This is a capacity-sharing probe, not a complete conversation load test.
"""
import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

import pytest
from psycopg import AsyncCursor
from redis.asyncio import Redis
from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import service_settings
from api_service.workers import agent as worker
from api_service.runs import execution
from api_service.workers.executor_events.runtime import ExecutorWorker
from tests.api_service.test_user_identity_postgres import database_url, harness, add_session
from tests.api_service.test_run_cleanup_postgres import runtime, enqueue
from service_contracts.events import ExecutorEvent

ROOT = Path(__file__).resolve().parents[3]
CURRENT = (ROOT/'src/api_service/runs/commands/claim.py').exists()
if not CURRENT:
    # This import is used only when profiling the historical source snapshot.
    from api_service.services.session_execution import run_event_owned
REPEATS = int(os.getenv('DTEST_CAPACITY_REPEATS', '2'))
DELAY = float(os.getenv('DTEST_CAPACITY_DELAY_SECONDS', '5'))
CASES = {'user_only': (8, 0), 'event_only': (0, 8), 'balanced': (4, 4)}


def percentile(values, fraction):
    values = sorted(values)
    return values[max(0, __import__('math').ceil(len(values)*fraction)-1)]


@pytest.mark.asyncio
@pytest.mark.parametrize('scenario', CASES)
@pytest.mark.parametrize('repeat', range(1, REPEATS+1))
async def test_capacity_probe(runtime, monkeypatch, tmp_path, scenario, repeat):
    h = runtime
    redis_url = os.environ['DTEST_COMMAND_TEST_REDIS_URL']
    from urllib.parse import urlparse
    assert urlparse(redis_url).hostname in {'localhost', '127.0.0.1'}
    assert DELAY >= 0 and 1 <= REPEATS <= 5
    output = Path(os.environ['DTEST_CAPACITY_OUTPUT'])
    namespace = 'capacity-'+uuid4().hex
    raw_url = make_url(h.engine.url).set(drivername='postgresql').render_as_string(hide_password=False)
    migration = tmp_path/'event.yml'
    migration.write_text('database_url: '+raw_url+'\nAGENT_WORKER_ENABLED: false\nEVENT_WORKER_ENABLED: false\n')
    result = subprocess.run([sys.executable, '-m', 'alembic', '-c', 'alembic.ini', 'upgrade', 'head' if CURRENT else 'ew_0001'],
        cwd=ROOT, env={**os.environ, 'SERVICE_CONFIG_FILE':str(migration), 'PYTHONPATH':str(ROOT/'src')},
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    async with h.engine.begin() as db:
        await db.execute(text('TRUNCATE ew_inbox,ew_bindings CASCADE' if CURRENT else 'TRUNCATE ew_inbox,ew_bindings,ew_commands CASCADE'))
    pooled = create_async_engine(h.engine.url, pool_size=8, max_overflow=0,
                                 pool_pre_ping=True, connect_args={'ssl':False})
    h.factory = async_sessionmaker(pooled, expire_on_commit=False, autoflush=False)
    configured = service_settings.get_settings()
    settings = configured.worker.model_copy(update={
        'database_url':raw_url, 'redis_url':redis_url, 'namespace':namespace,
        'executor_event_stream':namespace+':events', 'event_group_name':namespace+':ingress',
        'ingress_concurrency':2, 'pool_size':8, 'poll_seconds':.05, 'idle_poll_seconds':.1,
        'health_port':0, 'shutdown_seconds':2,
        **({} if CURRENT else {'dispatch_concurrency':2})})
    monkeypatch.setattr(service_settings, '_snapshot', replace(configured, worker=settings,
        api=configured.api.model_copy(update={'agent_worker_concurrency':4 if CURRENT else 2,
            'agent_worker_poll_interval_seconds':.05, 'task_cancel_poll_interval_seconds':.25,
            'run_cleanup_timeout_seconds':2, 'run_monitor_timeout_seconds':2})))
    records = []
    active = {'user':0, 'event':0}
    peaks = {'total':0, 'user':0, 'event':0}
    measuring = False
    epoch = 0.0
    sql_count = {'sqlalchemy':0, 'psycopg':0}
    def count_sa(*_):
        if measuring:
            sql_count['sqlalchemy'] += 1
    event.listen(pooled.sync_engine, 'before_cursor_execute', count_sa)
    old_psy_execute = AsyncCursor.execute
    async def count_pg(self, *args, **kwargs):
        if measuring:
            sql_count['psycopg'] += 1
        return await old_psy_execute(self, *args, **kwargs)
    monkeypatch.setattr(AsyncCursor, 'execute', count_pg)

    async def fixed_graph(kind, identity):
        row = {'kind':kind, 'identity':str(identity), 'start_seconds':time.perf_counter()-epoch}
        records.append(row)
        active[kind] += 1
        peaks[kind] = max(peaks[kind], active[kind])
        peaks['total'] = max(peaks['total'], sum(active.values()))
        try:
            await asyncio.sleep(DELAY)
            row['end_seconds'] = time.perf_counter()-epoch
        finally:
            active[kind] -= 1
    async def user_graph(**kwargs):
        await fixed_graph('user', kwargs['session_id'])
        return {'routing_result':{'route':'analysis'}}
    async def event_graph(context):
        await fixed_graph('event', context.execution_id)
    async def old_event_handler(context):
        await run_event_owned(context, lambda:event_graph(context))
    monkeypatch.setattr(execution, 'ainvoke_user_turn', user_graph)
    if CURRENT:
        monkeypatch.setattr(worker, 'execute_event', event_graph)
    handlers = {'execution.completed'} if CURRENT else {'execution.completed':old_event_handler}
    ingress = ExecutorWorker(settings, handlers)
    stop = asyncio.Event()
    loops = []
    user_count, event_count = CASES[scenario]
    expected = user_count + event_count
    try:
        async with ingress:
            # Alternate user/event admission for the balanced scenario. Both
            # versions start with all durable jobs ready, on distinct sessions.
            for index in range(max(user_count, event_count)):
                if index < user_count:
                    await enqueue(h, await add_session(h, h.user))
                if index < event_count:
                    eid = uuid4()
                    await ingress.store.register(execution_id=eid,
                        session_id=await add_session(h,h.user), task_id=str(uuid4()))
                    incoming = ExecutorEvent(event_id=uuid4(), execution_id=eid,
                        event_type='execution.completed', event_sequence=1,
                        schema_version='1.0', occurred_at='2026-10-03T00:00:00Z', payload={})
                    await ingress.store.ingest(incoming)
                    assert await ingress.store.advance(eid, {'execution.completed'}, 10) == (1,None)
            if not CURRENT:
                assert await ingress.outbox.once() == event_count
            # Warm all API connections, identically on both versions.
            async def warm():
                async with pooled.connect() as db:
                    await db.execute(text('SELECT 1'))
                    await asyncio.sleep(.03)
            await asyncio.gather(*(warm() for _ in range(8)))
            epoch, cpu_start = time.perf_counter(), time.process_time()
            measuring = True
            loops = [asyncio.create_task(ingress.run(stop_event=stop)),
                     asyncio.create_task(worker.run_forever(stop_event=stop))]
            async with asyncio.timeout(max(20, DELAY*expected+20)):
                while True:
                    for task in loops:
                        if task.done():
                            task.result()
                            raise AssertionError('Worker unexpectedly stopped')
                    async with h.factory() as db:
                        user_done = await db.scalar(text("SELECT count(*) FROM agent_runs WHERE status='success'"))
                        event_done = await db.scalar(text("SELECT count(*) FROM agent_commands WHERE namespace=:ns AND kind='executor_resume' AND state='DONE'" if CURRENT else "SELECT count(*) FROM ew_commands WHERE namespace=:ns AND state='DONE'"), {'ns':namespace})
                        pending = await db.scalar(text("SELECT count(*) FROM agent_commands WHERE namespace=:ns AND state<>'DONE'"), {'ns':namespace}) if CURRENT else 0
                    if user_done == user_count and event_done == event_count and pending == 0:
                        break
                    await asyncio.sleep(.05)
            elapsed = time.perf_counter()-epoch
            cpu = time.process_time()-cpu_start
            measuring = False
            assert len(records) == expected and len({r['identity'] for r in records}) == expected
            assert all('end_seconds' in r for r in records)
            assert peaks['total'] <= 4 and sum(active.values()) == 0
            async with h.factory() as db:
                assert await db.scalar(text('SELECT count(*) FROM session_executions WHERE token IS NOT NULL OR recovery_required')) == 0
                assert await db.scalar(text("SELECT count(*) FROM agent_runs WHERE status<>'success'")) == 0
            row = {'architecture':'common' if CURRENT else 'split', 'scenario':scenario,
                'repeat':repeat, 'jobs':expected, 'user_jobs':user_count, 'event_jobs':event_count,
                'graph_delay_seconds':DELAY, 'total_capacity':4,
                'user_capacity':4 if CURRENT else 2, 'event_capacity':4 if CURRENT else 2,
                'wall_seconds':elapsed, 'jobs_per_second':expected/elapsed,
                'graph_queue_mean_seconds':sum(r['start_seconds'] for r in records)/expected,
                'graph_queue_p95_seconds':percentile([r['start_seconds'] for r in records], .95),
                'graph_return_mean_seconds':sum(r['end_seconds'] for r in records)/expected,
                'graph_return_p95_seconds':percentile([r['end_seconds'] for r in records], .95),
                'peak_graphs':peaks, 'process_cpu_seconds':cpu, 'sql_statements':sql_count,
                'errors':0, 'duplicates':0, 'remaining_owners':0, 'records':records}
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open('a') as target:
                target.write(json.dumps(row)+'\n')
            print(json.dumps({k:v for k,v in row.items() if k!='records'}))
    finally:
        measuring = False
        stop.set()
        if loops:
            await asyncio.wait_for(asyncio.gather(*loops), 10)
        await pooled.dispose()
        async with Redis.from_url(redis_url, decode_responses=True) as redis:
            owned = [key async for key in redis.scan_iter(match=namespace+':*')]
            if owned:
                await redis.delete(*owned)
