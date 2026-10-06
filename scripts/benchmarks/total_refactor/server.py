"""Observe the unmodified selected application with real model HTTP calls."""
import argparse, asyncio, importlib, json, os, resource, sys, time, threading
from contextlib import asynccontextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from starlette.responses import JSONResponse
p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--config',type=Path,required=True);a=p.parse_args()
cfg=json.loads(a.config.read_text());sys.path.insert(0,str(a.source/'src'));sys.path.insert(1,str(a.source))
legacy=cfg['label']=='before'
if not legacy:
    from dtest.settings.loader import configure, load_settings
    configure(load_settings(config=cfg['settings'],environ={}))
from sqlalchemy import event,text
from app.core.database import get_engine,get_session_factory
from app.services import agent_graph_service as gs
from app import agent_run_worker as worker
from psycopg_pool import AsyncConnectionPool

kind=ContextVar('bench_kind',default='worker');run=ContextVar('bench_run',default=None)
m={'active':False};pools=[];starts={}
def enabled():return m.get('active',False)
def record(bucket,**fields):
    if enabled():m[bucket].append({'at':time.perf_counter(),'run_id':run.get(),**fields})
def reset():
    m.clear();m.update(active=True,start=time.perf_counter(),cpu_start=time.process_time(),holds=[],acquires=[],sql=[],graphs=[],workers=[],resources=[],models=[],samples=[],http=[],run_stacks=[],graph_active=0,worker_active=0,peak_graph=0,peak_worker=0)
engine=get_engine();pool=engine.sync_engine.pool;get=pool._do_get

def acquire():
    t=time.perf_counter()
    try:return get()
    finally:record('acquires',ms=(time.perf_counter()-t)*1000,kind=kind.get())
pool._do_get=acquire
@event.listens_for(engine.sync_engine,'checkout')
def checkout(conn,rec,proxy):
    starts[id(rec)]=(time.perf_counter(),kind.get(),run.get())
@event.listens_for(engine.sync_engine,'checkin')
def checkin(conn,rec):
    item=starts.pop(id(rec),None)
    if item and enabled():
        t,k,r=item
        m['holds'].append({'start':max(t,m['start']),'end':time.perf_counter(),'kind':k,'run_id':r})
@event.listens_for(engine.sync_engine,'before_cursor_execute')
def sql_before(conn,cursor,statement,parameters,context,many):context._bench=(time.perf_counter(),kind.get(),run.get())
@event.listens_for(engine.sync_engine,'after_cursor_execute')
def sql_after(conn,cursor,statement,parameters,context,many):
    t,k,r=context._bench
    if enabled():m['sql'].append({'ms':(time.perf_counter()-t)*1000,'kind':k,'run_id':r,'verb':statement.lstrip().split()[0]})

original_init=AsyncConnectionPool.__init__
def pool_init(self,*args,**kwargs):
    original_init(self,*args,**kwargs);pools.append(self)
    record('resources',operation='pool_construct',name=self.name)
AsyncConnectionPool.__init__=pool_init
for operation in ('open','close'):
    original=getattr(AsyncConnectionPool,operation)
    def wrapper(fn,op):
        @wraps(fn)
        async def wrapped(self,*args,**kwargs):
            t=time.perf_counter()
            try:return await fn(self,*args,**kwargs)
            finally:record('resources',operation='pool_'+op,name=self.name,ms=(time.perf_counter()-t)*1000)
        return wrapped
    setattr(AsyncConnectionPool,operation,wrapper(original,operation))

module=importlib.import_module('app.graphs.builders.build_analysis_workflow_graph' if legacy else 'dtest.agent_service.agents.analysis.graph')
build=module.build_analysis_workflow_graph
@wraps(build)
def measured_build(*args,**kwargs):
    t=time.perf_counter();graph=build(*args,**kwargs);record('resources',operation='graph_build',ms=(time.perf_counter()-t)*1000)
    invoke=graph.ainvoke
    @wraps(invoke)
    async def wrapped(*a,**kw):
        start=time.perf_counter();measured=enabled();outcome='ok'
        if measured:m['graph_active']+=1;m['peak_graph']=max(m['peak_graph'],m['graph_active'])
        try:return await invoke(*a,**kw)
        except BaseException as exc:outcome=type(exc).__name__;raise
        finally:
            if measured:
                m['graph_active']-=1;record('graphs',ms=(time.perf_counter()-start)*1000,outcome=outcome)
    graph.ainvoke=wrapped;return graph
module.build_analysis_workflow_graph=measured_build
original_execute=worker.execute_claimed
@wraps(original_execute)
async def execute(item):
    rid=item[0] if legacy else item.claim.run_id
    token=run.set(str(rid));t=time.perf_counter();outcome='ok';m['worker_active']+=1;m['peak_worker']=max(m['peak_worker'],m['worker_active'])
    try:return await original_execute(item)
    except BaseException as exc:outcome=type(exc).__name__;raise
    finally:
        m['worker_active']-=1;record('workers',ms=(time.perf_counter()-t)*1000,outcome=outcome);run.reset(token)
worker.execute_claimed=execute
# Measure real ChatOpenAI transport boundaries, retaining sync vs async calls.
from langchain_openai import ChatOpenAI
orig_sync=ChatOpenAI._generate;orig_async=ChatOpenAI._agenerate
@wraps(orig_sync)
def model_sync(self,*args,**kwargs):
    t=time.perf_counter()
    try:return orig_sync(self,*args,**kwargs)
    finally:record('models',mode='sync',ms=(time.perf_counter()-t)*1000,thread=threading.current_thread().name)
@wraps(orig_async)
async def model_async(self,*args,**kwargs):
    t=time.perf_counter()
    try:return await orig_async(self,*args,**kwargs)
    finally:record('models',mode='async',ms=(time.perf_counter()-t)*1000,thread=threading.current_thread().name)
ChatOpenAI._generate=model_sync;ChatOpenAI._agenerate=model_async

if legacy:
    from dtest.api_service.app import app
else:
    from dtest.bootstrap import create_app
    from dtest.settings.loader import get_settings
    app=create_app(get_settings())

@app.middleware('http')
async def measured(request,call_next):
    route=request.url.path;k='control' if route.startswith('/_bench') else 'run_get' if '/runs/' in route and request.method=='GET' else 'run_post' if route.endswith('/runs') else 'crud'
    token=kind.set(k);t=time.perf_counter()
    try:
        response=await call_next(request);elapsed=(time.perf_counter()-t)*1000;response.headers['X-Bench-Server-Ms']=str(elapsed)
        if enabled() and k!='control':record('http',kind=k,ms=elapsed,status=response.status_code)
        return response
    finally:kind.reset(token)
@app.get('/_bench/ready')
async def ready():return {'ready':True,'version':cfg['commit']}
@app.post('/_bench/reset')
async def clear():reset();return {'ok':True}

def stacks():
    rows=[]
    for task in asyncio.all_tasks():
        if 'agent-run' not in task.get_name():continue
        coro=task.get_coro();frames=[]
        for _ in range(30):
            if coro is None:break
            frame=getattr(coro,'cr_frame',None) or getattr(coro,'gi_frame',None)
            if frame:frames.append({'file':Path(frame.f_code.co_filename).name,'function':frame.f_code.co_name,'line':frame.f_lineno})
            coro=getattr(coro,'cr_await',None) or getattr(coro,'gi_yieldfrom',None)
        rows.append({'task':task.get_name(),'frames':frames})
    return rows
@app.get('/_bench/progress')
async def progress():return {'models':len(m['models']),'graphs':len(m['graphs']),'workers':len(m['workers']),'active_workers':m['worker_active'],'active_graphs':m['graph_active']}
@app.get('/_bench/metrics')
async def metrics():
    now=time.perf_counter();extra=[]
    for t,k,r in starts.values():extra.append({'start':max(t,m['start']),'end':now,'kind':k,'run_id':r,'open_at_snapshot':True})
    healthy=None;faults={}
    if not legacy:
        from app.core.execution_lifecycle import execution_health
        healthy=execution_health.healthy;faults=dict(execution_health.faults)
    return JSONResponse({**m,'holds':m.get('holds',[])+extra,'cpu_seconds':time.process_time()-m.get('cpu_start',time.process_time()),'rss_peak_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'healthy':healthy,'faults':faults,'current_stacks':stacks()})
async def sampler():
    previous=time.perf_counter();counter=0
    while True:
        await asyncio.sleep(.1);now=time.perf_counter();counter+=1
        if enabled():
            m['samples'].append({'at':now,'checked_out':pool.checkedout(),'lag_ms':max(0,(now-previous-.1)*1000),'threads':threading.active_count(),'open_psycopg_pools':sum(not p.closed for p in pools),'psycopg_connections':sum(p.get_stats().get('pool_size',0) for p in pools if not p.closed)})
            if counter%100==0:m['run_stacks'].append({'at':now,'stacks':stacks()})
        previous=now
async def main():
    import uvicorn
    previous=app.router.lifespan_context
    @asynccontextmanager
    async def life(application):
        async with previous(application) as state:
            async def warm():
                async with get_session_factory()() as db:await db.execute(text('SELECT pg_sleep(.01)'))
            await asyncio.gather(*(warm() for _ in range(cfg['pool'])))
            if not legacy:
                from app.services.user_service import UserService
                from app.schemas.common.user_schema import UserCreate
                async with get_session_factory()() as db:await UserService.bootstrap_admin(db,UserCreate(user_id='admin',user_name='Benchmark admin',role='admin'))
            task=asyncio.create_task(sampler())
            try:yield state
            finally:
                task.cancel();await asyncio.gather(task,return_exceptions=True)
    app.router.lifespan_context=life
    await uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=cfg['port'],log_level='error',timeout_graceful_shutdown=8)).serve()
asyncio.run(main())
