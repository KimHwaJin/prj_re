"""Actual commit notifications, deadlines, fan-out, fallback and shared SSE life."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
import json
import os
from pathlib import Path
import time
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from sqlalchemy import select, text, update

import service_settings
from api_service import agent_run_worker as worker
from api_service.runs import execution
from api_service.runs.commands.wakeup import namespace_signal, next_retry_delay, subscription
from api_service.services.helpers import utc_now
from api_service.models.common.agent_command_model import AgentCommandModel as Command
from api_service.models.common.agent_run_model import AgentRunModel as Run
from service_contracts.events import DeferEvent
from service_runtime.postgres_signals import PostgresSignals, COMMAND_CHANNEL, process_signals
from api_service.test.test_user_identity_postgres import database_url, harness, add_session
from api_service.test.test_run_cleanup_postgres import runtime, enqueue
from api_service.test.test_agent_commands_postgres import commands, admit_event

pytestmark=pytest.mark.asyncio


def configure(monkeypatch, **values):
    current=service_settings.get_settings()
    monkeypatch.setattr(service_settings,'_snapshot',replace(current,api=current.api.model_copy(update={
        'agent_worker_poll_interval_seconds':.05,'agent_worker_reconcile_interval_seconds':5,
        **values})))


async def until(predicate, timeout=3):
    async with asyncio.timeout(timeout):
        while not await predicate():await asyncio.sleep(.005)


async def healthy_idle(signals, calls):
    await asyncio.wait_for(signals.ready.wait(),3)
    async def scanned():return bool(calls)
    await until(scanned)
    await asyncio.sleep(.08)  # allow initial LISTEN invalidation/scan to settle


async def count_claims(monkeypatch):
    calls=[]; original=worker.claim_one
    async def counted():
        value=await original(); calls.append((time.perf_counter(),value)); return value
    monkeypatch.setattr(worker,'claim_one',counted)
    return calls


async def stop(task,event):
    event.set(); await asyncio.wait_for(task,3)


async def completed(h, identities):
    async with h.factory() as db:
        states=list(await db.scalars(select(Run.status).where(Run.run_id.in_(identities))))
    return len(states)==len(identities) and all(state=='success' for state in states)


async def test_commit_rollback_namespace_and_both_independent_listeners(commands):
    h=commands; cfg=service_settings.get_settings()
    one=PostgresSignals(cfg.api.database_url); two=PostgresSignals(cfg.api.database_url)
    a,b=asyncio.Event(),asyncio.Event()
    selected=namespace_signal(cfg.worker.namespace)
    def callback(wake,payload):
        if payload==selected:wake.set()
    async with one.subscribe(COMMAND_CHANNEL,lambda p:callback(a,p)), two.subscribe(COMMAND_CHANNEL,lambda p:callback(b,p)):
        await asyncio.wait_for(asyncio.gather(one.ready.wait(),two.ready.wait()),3)
        assert one.connection is not two.connection
        await enqueue(h)
        await asyncio.wait_for(asyncio.gather(a.wait(),b.wait()),1)
        a.clear();b.clear()
        async with h.factory() as db:
            await db.execute(update(Command).values(available_at=utc_now()+timedelta(minutes=1)))
            await asyncio.sleep(.05);assert not a.is_set() and not b.is_set()
            await db.rollback()
        await asyncio.sleep(.05);assert not a.is_set() and not b.is_set()
        async with h.factory() as db:
            await db.execute(text("SELECT pg_notify(:channel,:payload)"),
                {'channel':COMMAND_CHANNEL,'payload':namespace_signal('another-service')})
            await db.commit()
        await asyncio.sleep(.05);assert not a.is_set() and not b.is_set()
        # psycopg routing uses the same trigger, without an API callback.
        await admit_event(h,await add_session(h,h.user))
        await asyncio.wait_for(asyncio.gather(a.wait(),b.wait()),1)
    assert one.connection is two.connection is None


async def test_shared_sse_and_worker_connection_has_independent_lifetimes(commands):
    h=commands;cfg=service_settings.get_settings();wake=asyncio.Event()
    public=await enqueue(h)
    async with h.factory() as db:
        user_id=await db.scalar(select(Run.metadata_json).where(Run.run_id==UUID(public['run_id'])))
    key=(UUID(user_id['requested_by_user_id']),UUID(h.session_id),UUID(public['run_id']))
    hub=h.app.state.run_stream_hub
    async with subscription(cfg.api.database_url,cfg.worker.namespace,wake) as signals:
        await asyncio.wait_for(signals.ready.wait(),3)
        connection=signals.connection
        async with hub.subscribe(*key):
            await asyncio.wait_for(hub.ready.wait(),3)
            assert hub.connection is connection and len(signals.subscriptions)==2
        assert hub.connection is None and signals.connection is connection
        assert signals.ready.is_set() and len(signals.subscriptions)==1
    assert signals.connection is None and signals.task is None
    # SSE-only starts its own reference after the Worker leaves.
    async with hub.subscribe(*key):
        await asyncio.wait_for(hub.ready.wait(),3)
        assert hub.connection is not None and hub.connection is not connection
    assert hub.connection is None and not signals.subscriptions


async def test_signal_loss_periodic_scan_and_listener_disconnect(commands,monkeypatch):
    h=commands;configure(monkeypatch,agent_worker_reconcile_interval_seconds=.2)
    monkeypatch.setattr(execution,'ainvoke_user_turn',AsyncMock(return_value={'routing_result':{'route':'analysis'}}))
    cfg=service_settings.get_settings();signals=process_signals(cfg.api.database_url)
    calls=await count_claims(monkeypatch)
    stop_event=asyncio.Event();loop=asyncio.create_task(worker.run_forever(stop_event=stop_event))
    try:
        await healthy_idle(signals,calls)
        original=signals._emit
        def drop_commands(channel,payload):
            if channel!=COMMAND_CHANNEL:original(channel,payload)
        monkeypatch.setattr(signals,'_emit',drop_commands)
        public=await enqueue(h)
        await until(lambda:completed(h,[UUID(public['run_id'])]),1.5)
        monkeypatch.setattr(signals,'_emit',original)
        signals.connection.terminate()
        # Work committed while the LISTEN connection is down still progresses.
        other=await enqueue(h,await add_session(h,h.user))
        await until(lambda:completed(h,[UUID(other['run_id'])]),1.5)
        await asyncio.wait_for(signals.ready.wait(),3)
        assert len([value for _,value in calls if value is not None])==2
    finally:await stop(loop,stop_event)


async def test_retry_deadline_is_rechecked_without_waiting_reconcile(commands,monkeypatch):
    h=commands;configure(monkeypatch,agent_worker_reconcile_interval_seconds=20)
    await admit_event(h)
    calls=[]
    async def execute(_):
        calls.append(time.perf_counter())
        if len(calls)==1:raise DeferEvent('fixture handoff pending')
    monkeypatch.setattr(worker,'execute_event',execute)
    stopped=asyncio.Event();loop=asyncio.create_task(worker.run_forever(stop_event=stopped))
    async def done():
        async with h.factory() as db:
            return await db.scalar(select(Command.state))=='DONE'
    try:
        await until(done,2)
        assert len(calls)==2 and .15<=calls[1]-calls[0]<1
        async with h.factory() as db:
            assert await db.scalar(select(Command.failure_attempts))==0
    finally:await stop(loop,stopped)


async def test_deadline_that_passes_between_claim_and_lookup_is_not_lost(commands):
    h=commands;await admit_event(h)
    observed=utc_now()-timedelta(seconds=1)
    async with h.factory() as db:
        await db.execute(update(Command).values(available_at=utc_now()-timedelta(seconds=.1)))
        await db.commit()
    assert await next_retry_delay(h.factory,service_settings.get_settings().worker.namespace,observed_at=observed)==.05


async def test_two_workers_fanout_no_duplicate_and_no_claim_while_full(commands,monkeypatch):
    h=commands;configure(monkeypatch,agent_worker_concurrency=1)
    cfg=service_settings.get_settings();brokers=[]
    @asynccontextmanager
    async def independent(_url,namespace,wake):
        signals=PostgresSignals(cfg.api.database_url);brokers.append(signals)
        def changed(payload):
            if payload is None or payload==namespace_signal(namespace):wake.set()
        async with signals.subscribe(COMMAND_CHANNEL,changed):yield signals
    monkeypatch.setattr(worker,'subscription',independent)
    gate=asyncio.Event();starts=[]
    async def graph(**kwargs):
        starts.append(kwargs['run_id']);await gate.wait()
        return {'routing_result':{'route':'analysis'}}
    monkeypatch.setattr(execution,'ainvoke_user_turn',graph)
    calls=await count_claims(monkeypatch)
    public=[await enqueue(h,await add_session(h,h.user)) for _ in range(4)]
    ids=[UUID(p['run_id']) for p in public]
    stopped=asyncio.Event();loops=[asyncio.create_task(worker.run_forever(stop_event=stopped)) for _ in range(2)]
    async def filled():return len(starts)==2 and len(brokers)==2 and all(s.ready.is_set() for s in brokers)
    try:
        await until(filled)
        count=len(calls)
        async with h.factory() as db:
            for _ in range(20):
                await db.execute(text('SELECT pg_notify(:channel,:payload)'),
                    {'channel':COMMAND_CHANNEL,'payload':namespace_signal(cfg.worker.namespace)})
                await db.commit()
        await asyncio.sleep(.1)
        assert len(calls)==count and len(starts)==2
        gate.set();await until(lambda:completed(h,ids))
        assert len(starts)==len(set(starts))==4
        async with h.factory() as db:
            assert list(await db.scalars(select(Run.attempt_count)))==[1]*4
    finally:
        gate.set();stopped.set();await asyncio.wait_for(asyncio.gather(*loops),3)
    assert all(s.connection is None and not s.subscriptions for s in brokers)


async def test_idle_query_cost_and_admission_latency_measurement(commands,monkeypatch):
    h=commands;measurements=[]
    monkeypatch.setattr(execution,'ainvoke_user_turn',AsyncMock(return_value={'routing_result':{'route':'analysis'}}))
    original=worker.claim_one
    for enabled in (False,True):
        configure(monkeypatch,agent_worker_notify_enabled=enabled,
            agent_worker_poll_interval_seconds=.25,agent_worker_reconcile_interval_seconds=5)
        calls=[]
        async def claim():
            value=await original();calls.append((time.perf_counter(),value));return value
        monkeypatch.setattr(worker,'claim_one',claim)
        stop_event=asyncio.Event();loop=asyncio.create_task(worker.run_forever(stop_event=stop_event))
        signals=process_signals(service_settings.get_settings().api.database_url)
        try:
            if enabled:await healthy_idle(signals,calls)
            else:
                async def started():return bool(calls)
                await until(started)
                await asyncio.sleep(.08)
            count=len(calls)
            await asyncio.sleep(6)
            idle_count=len(calls)-count
            session_id=await add_session(h,h.user)
            # Submit just after a confirmed empty claim; old poll must wait,
            # while the committed command notification can wake immediately.
            if not enabled:
                count=len(calls)
                async def scanned():return len(calls)>count
                await until(scanned)
            start=time.perf_counter()
            public=await enqueue(h,await add_session(h,h.user))
            await until(lambda:completed(h,[UUID(public['run_id'])]))
            actual=[moment for moment,value in calls if value is not None and str(value.command_id)==public['run_id']]
            assert len(actual)==1
            measurements.append({'notify_enabled':enabled,'idle_window_seconds':6,
                'idle_empty_claims':idle_count,'submission_to_claim_seconds':actual[0]-start,
                'listener_connections':int(signals.connection is not None)})
        finally:await stop(loop,stop_event)
    assert measurements[0]['idle_empty_claims']>=20 and measurements[1]['idle_empty_claims']<=1
    assert measurements[1]['submission_to_claim_seconds']<1
    if path:=os.getenv('DTEST_COMMAND_WAKEUP_REPORT'):
        Path(path).write_text(json.dumps(measurements,indent=2)+'\n')


async def test_two_os_processes_wake_and_complete_each_command_once(commands,tmp_path):
    """Independent Python workers/engines/listeners, not Kubernetes/HPA testing."""
    import subprocess,sys
    h=commands;cfg=service_settings.get_settings()
    sessions=[await add_session(h,h.user) for _ in range(6)]
    config_file=tmp_path/'private-settings.json'
    config_file.write_text(json.dumps({'DATABASE_URL':cfg.api.database_url,
        'EW_NAMESPACE':cfg.worker.namespace,'AGENT_WORKER_ENABLED':False,'EVENT_WORKER_ENABLED':False,
        'TASK_RECONCILER_ENABLED':False,'MODEL_PROVIDER':'mock','DATABASE_POOL_SIZE':4,
        'DATABASE_MAX_OVERFLOW':0,'AGENT_WORKER_CONCURRENCY':1,
        'AGENT_WORKER_RECONCILE_INTERVAL_SECONDS':20}))
    config_file.chmod(0o600)
    child=tmp_path/'worker.py'
    child.write_text('''import asyncio,json,sys
from pathlib import Path
import service_settings
service_settings.configure(service_settings.load_settings(config=json.loads(Path(sys.argv[1]).read_text()),environ={}))
from api_service import agent_run_worker as worker
from api_service.runs import execution
from api_service.core.database import close_database
from service_runtime.postgres_signals import process_signals
async def graph(**kwargs):
    await asyncio.sleep(.2)
    return {'routing_result':{'route':'analysis'}}
execution.ainvoke_user_turn=graph
original=worker.execute_claimed
async def observed(item):
    await original(item)
    with Path(sys.argv[2]).open('a') as f:f.write(str(item.command_id)+'\\n')
worker.execute_claimed=observed
async def main():
    stop=asyncio.Event()
    task=asyncio.create_task(worker.run_forever(stop_event=stop))
    signals=process_signals(service_settings.get_settings().api.database_url)
    await asyncio.wait_for(signals.ready.wait(),10)
    Path(sys.argv[3]).touch()
    try:
        while not Path(sys.argv[4]).exists():
            if task.done():task.result();raise RuntimeError('worker stopped')
            await asyncio.sleep(.02)
    finally:
        stop.set()
        await asyncio.wait_for(task,5)
        await close_database()
asyncio.run(main())
''')
    marker=tmp_path/'stop';processes=[];logs=[];output=[];ready=[]
    try:
        for index in range(2):
            result=tmp_path/f'worker-{index}.txt';output.append(result)
            flag=tmp_path/f'ready-{index}';ready.append(flag)
            log_path=tmp_path/f'worker-{index}.log';log=log_path.open('w');logs.append((log,log_path))
            environment={name:value for name,value in os.environ.items() if name in {'PATH','HOME','TMPDIR','LANG','LC_ALL'}}
            environment.update(PYTHONPATH=str(Path(__file__).resolve().parents[2]),PYTHONDONTWRITEBYTECODE='1')
            processes.append(subprocess.Popen([sys.executable,str(child),str(config_file),str(result),str(flag),str(marker)],
                env=environment,stdout=log,stderr=log))
        async def started():
            for process,(_,path) in zip(processes,logs):
                assert process.poll() is None,path.read_text()
            return all(path.exists() for path in ready)
        await until(started,15)
        public=await asyncio.gather(*(enqueue(h,session) for session in sessions))
        ids=[UUID(p['run_id']) for p in public]
        await until(lambda:completed(h,ids),10)
        async def recorded():return sum(len(path.read_text().splitlines()) if path.exists() else 0 for path in output)==6
        await until(recorded)
        seen=[identity for path in output if path.exists() for identity in path.read_text().splitlines()]
        assert len(seen)==len(set(seen))==6
        assert set(seen)=={str(identity) for identity in ids}
        async with h.factory() as db:
            assert list(await db.scalars(select(Run.attempt_count)))==[1]*6
    finally:
        marker.touch()
        for process,(log,path) in zip(processes,logs):
            try:
                await asyncio.wait_for(asyncio.to_thread(process.wait),10)
            except TimeoutError:
                process.kill();await asyncio.to_thread(process.wait)
                raise AssertionError('Child worker did not drain')
            finally:log.close()
            assert process.returncode==0,path.read_text()
