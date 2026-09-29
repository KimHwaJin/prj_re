"""Loopback-only comparison server. Imports ONLY the selected immutable snapshot.

No production settings files are loaded; no external LLM/Executor/Redis is used.
This harness adds measurement endpoints to an otherwise real FastAPI/Worker app.
"""
import argparse
import asyncio
from collections import Counter
from contextlib import asynccontextmanager
from contextvars import ContextVar
import json
import logging
import os
import resource
from pathlib import Path
import time
import sys

p=argparse.ArgumentParser()
p.add_argument('--source',required=True)
p.add_argument('--config',required=True)
a=p.parse_args()
sys.path.insert(0,str(Path(a.source)/'src'))
cfg=json.loads(Path(a.config).read_text())
from sqlalchemy import event
from sqlalchemy.engine import make_url
from service_settings import load_settings
url=os.environ['DTEST_BENCH_DATABASE_URL']
u=make_url(url)
if u.database != 'identity_test' or u.host not in ('127.0.0.1', 'localhost'):
    raise SystemExit('Only an explicitly disposable local identity_test database is allowed')
raw=u.set(drivername='postgresql').render_as_string(hide_password=False)
settings=load_settings(config={
 'DATABASE_URL':url,'CHECKPOINT_DB_URI':raw,'EW_DATABASE_URL':raw,
 'DATABASE_POOL_SIZE':cfg['pool'],'DATABASE_MAX_OVERFLOW':0,'DATABASE_POOL_TIMEOUT_SECONDS':2,
 'AGENT_WORKER_CONCURRENCY':cfg['slots'],'AGENT_WORKER_POLL_INTERVAL_SECONDS':.05,
 'AGENT_WORKER_MAX_RETRIES':0,'TASK_CANCEL_POLL_INTERVAL_SECONDS':.25,
 'RUN_MONITOR_TIMEOUT_SECONDS':3,'RUN_CLEANUP_TIMEOUT_SECONDS':3,
 'TASK_LEASE_SECONDS':300,'AGENT_WORKER_ENABLED':True,
 'TASK_RECONCILER_ENABLED':False,'EVENT_WORKER_ENABLED':False,
 'MODEL_PROVIDER':'mock','MODEL_MOCK_DELAY_MS':cfg['delay_ms'], 'DATA_MOCK':True,
 'EXECUTOR_SUBMIT_ENABLED':False,'DEMO_ARTIFACTS_ENABLED':False,
 'WORKFLOW_PERSISTENCE_ENABLED':False,'GRAPH_CHECKPOINTER':'postgres',
 'SHUTDOWN_DRAIN_SECONDS':1,'SHUTDOWN_TIMEOUT_SECONDS':4,
},environ={})
from service_settings import configure
configure(settings)
from service_bootstrap import create_app
from app.core.database import get_engine,get_session_factory
from app.services import agent_graph_service as gs
from app.core.execution_lifecycle import execution_health
from app.services.user_service import UserService
from app.schemas.common.user_schema import UserCreate
from agent_service.runtime.langgraph.checkpointer import create_checkpointer
from agent_service.agents.analysis.graph import build_analysis_workflow_graph
from agent_service.agents.analysis.dependencies import create_llm_dependencies
from app.services.workflow_persistence import NullWorkflowStore
from agent_service.agents.analysis.testing.mock_dependencies import ScriptedAgent

metrics={'active':False}
kind=ContextVar('bench_kind',default='worker')
def reset():
    metrics.clear()
    metrics.update(active=True,cpu_start=time.process_time(),wall_start=time.perf_counter(),holds=[],checkouts=[],sql=[],graphs=[],models=[],model_events=[],samples=[],http=[],errors=[],peak_graph=0,graph_active=0)
engine=get_engine()
pool=engine.sync_engine.pool
original_get=pool._do_get
# SQLAlchemy's greenlet-adapted queue blocks here while awaiting an available
# connection. Includes creation for cold connections; measured window is warmed.
def acquire():
    start=time.perf_counter()
    try:return original_get()
    finally:
        if metrics['active']:metrics['checkouts'].append({'ms':(time.perf_counter()-start)*1000,'kind':kind.get()})
pool._do_get=acquire
@event.listens_for(engine.sync_engine,'checkout')
def checkout(conn,record,proxy):
    record.info['bench_hold']=(time.perf_counter(),kind.get(),metrics['active'])
@event.listens_for(engine.sync_engine,'checkin')
def checkin(conn,record):
    row=record.info.pop('bench_hold',None)
    if row and row[2] and metrics['active']:
        metrics['holds'].append({'ms':(time.perf_counter()-row[0])*1000,'kind':row[1]})
@event.listens_for(engine.sync_engine,'before_cursor_execute')
def before(conn,cursor,statement,parameters,context,many):
    context._bench=(time.perf_counter(),kind.get(),metrics['active'])
@event.listens_for(engine.sync_engine,'after_cursor_execute')
def after(conn,cursor,statement,parameters,context,many):
    start,k,enabled=context._bench
    if enabled and metrics['active']:metrics['sql'].append({'ms':(time.perf_counter()-start)*1000,'kind':k,'verb':statement.lstrip().split()[0]})
orig_model=ScriptedAgent.ainvoke
async def timed_model(self,*args,**kwargs):
    t=time.perf_counter()
    try:return await orig_model(self,*args,**kwargs)
    finally:
        if metrics['active']:
            elapsed=(time.perf_counter()-t)*1000
            from app.core.execution_claim import current_execution_claim
            claim=current_execution_claim.get()
            metrics['models'].append(elapsed)
            metrics['model_events'].append({'run_id':str(claim.run_id) if claim else None,'ms':elapsed})
ScriptedAgent.ainvoke=timed_model

@asynccontextmanager
async def graph_context():
    async with create_checkpointer(raw,setup_on_start=True,min_size=1,max_size=4,timeout=3) as saver:
        graph=build_analysis_workflow_graph(create_llm_dependencies(settings.agent),settings.agent,
            checkpointer=saver,workflow_store=NullWorkflowStore())
        invoke=graph.ainvoke
        async def timed_invoke(*args,**kwargs):
            t=time.perf_counter();measured=metrics['active'];outcome='ok'
            if measured:
                metrics['graph_active']+=1
                metrics['peak_graph']=max(metrics['peak_graph'],metrics['graph_active'])
            try:return await invoke(*args,**kwargs)
            except BaseException as exc:
                outcome=type(exc).__name__;raise
            finally:
                if measured and metrics['active']:
                    metrics['graph_active']-=1
                    from app.core.execution_claim import current_execution_claim
                    claim=current_execution_claim.get()
                    metrics['graphs'].append({'run_id':str(claim.run_id) if claim else None,'ms':(time.perf_counter()-t)*1000,'outcome':outcome})
        graph.ainvoke=timed_invoke
        yield graph
# The normal Worker, RunService, graph boundary and all CRUD persistence run
# unchanged. Only resource construction omits the unused Executor bindings.
gs.runtime._graph_context=graph_context
app=create_app(settings)

@app.middleware('http')
async def observed(request,call_next):
    route=request.url.path
    k=('control' if route.startswith('/_bench') else
       'run_get' if '/runs/' in route and request.method=='GET' else
       'run_post' if route.endswith('/runs') else 'crud')
    token=kind.set(k);t=time.perf_counter()
    try:
        response=await call_next(request)
        elapsed=(time.perf_counter()-t)*1000
        response.headers['X-Bench-Server-Ms']=str(elapsed)
        if metrics['active'] and k!='control':
            metrics['http'].append({'kind':k,'ms':elapsed,'status':response.status_code})
        return response
    finally:kind.reset(token)

@app.get('/_bench/ready')
async def ready():
    return {'ready':app.state.service_runtime.ready}
@app.post('/_bench/reset')
async def clear():
    reset();return {'ok':True}
@app.get('/_bench/metrics')
async def results():
    from app.agent_run_worker import logger
    return {**metrics,'cpu_seconds':time.process_time()-metrics.get('cpu_start',time.process_time()),'rss_peak_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'healthy':execution_health.healthy,'faults':dict(execution_health.faults),'checked_out':pool.checkedout()}

async def sampler():
    previous=time.perf_counter()
    while True:
        await asyncio.sleep(.05)
        now=time.perf_counter()
        if metrics['active']:
            metrics['samples'].append({'at':now,'checked_out':pool.checkedout(),'lag_ms':max(0,(now-previous-.05)*1000)})
        previous=now

async def main():
    # Warm application pool and compile/checkpointer before measured traffic.
    from sqlalchemy import text
    async def warm():
        async with get_session_factory()() as db:
            await db.execute(text('SELECT pg_sleep(0.02)'))
    await asyncio.gather(*(warm() for _ in range(cfg['pool'])))
    async with get_session_factory()() as db:
        await UserService.bootstrap_admin(db,UserCreate(user_id='admin',user_name='Benchmark admin',role='admin'))
    # Runtime initialization occurs after lifespan's start, via a wrapper.
    previous=app.router.lifespan_context
    @asynccontextmanager
    async def life(application):
        async with previous(application) as state:
            async with gs.runtime.open_graph():pass
            monitor=asyncio.create_task(sampler())
            try:yield state
            finally:
                monitor.cancel();await asyncio.gather(monitor,return_exceptions=True)
    app.router.lifespan_context=life
    import uvicorn
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=cfg['port'],log_level='error',access_log=False))
    await server.serve()
if __name__=='__main__':
    logging.basicConfig(level=logging.ERROR)
    asyncio.run(main())
