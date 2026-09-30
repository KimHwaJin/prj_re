"""Observe the unmodified selected application with real model HTTP calls."""
import argparse, asyncio, importlib, json, os, resource, sys, time, threading
from contextlib import asynccontextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from starlette.responses import JSONResponse
p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--config',type=Path,required=True);a=p.parse_args()
cfg=json.loads(a.config.read_text());sys.path.insert(0,str(a.source/'src'));sys.path.insert(1,str(a.source))
from service_settings import configure, load_settings
configure(load_settings(config=cfg['settings'],environ={}))
from sqlalchemy import event,text
from api_service.core.database import get_engine,get_session_factory
from api_service.services import agent_graph_service as gs
from api_service import agent_run_worker as worker
from psycopg_pool import AsyncConnectionPool

kind=ContextVar('bench_kind',default='worker');run=ContextVar('bench_run',default=None)
m={'active':False};pools=[];starts={}
def enabled():return m.get('active',False)
def record(bucket,**fields):
    if enabled():m[bucket].append({'at':time.perf_counter(),'run_id':run.get(),**fields})
def reset():
    m.clear();m.update(active=True,start=time.perf_counter(),cpu_start=time.process_time(),holds=[],acquires=[],sql=[],spans=[],traces=[],commits=[],graphs=[],workers=[],resources=[],models=[],samples=[],http=[],run_stacks=[],graph_active=0,worker_active=0,peak_graph=0,peak_worker=0)
def actor():
    task=asyncio.current_task()
    name=task.get_name() if task else ''
    for prefix,category in [('cancel-watch:','cancel_watch'),('llm-token-events:','token_writer'),('agent-run-claim','queue_claim'),('task-heartbeat','heartbeat')]:
        if name.startswith(prefix):return category
    return kind.get()
# Keep the existing diagnostics' spans but collect in memory, avoiding disk writes.
from service_runtime import diagnostics as diag
original_end=diag.Trace.end
original_emit=diag.Trace.emit
def trace_end(self,key,outcome='ok'):
    item=self.active.get(key)
    if item and enabled():
        name,start=item
        m['spans'].append({'name':name,'start':start,'end':time.perf_counter(),'run_id':self.run_id,'outcome':outcome})
    return original_end(self,key,outcome)
def trace_emit(self,event,**fields):
    if enabled():m['traces'].append({'event':event,'run_id':self.run_id,**fields})
diag.Trace.end=trace_end;diag.Trace.emit=trace_emit
from sqlalchemy.ext.asyncio import AsyncSession
commit=AsyncSession.commit
async def measured_commit(self):
    start=time.perf_counter()
    try:return await commit(self)
    finally:record('commits',start=start,end=time.perf_counter(),kind=actor())
AsyncSession.commit=measured_commit
engine=get_engine();pool=engine.sync_engine.pool;get=pool._do_get

def acquire():
    t=time.perf_counter()
    try:return get()
    finally:record('acquires',ms=(time.perf_counter()-t)*1000,kind=actor())
pool._do_get=acquire
@event.listens_for(engine.sync_engine,'checkout')
def checkout(conn,rec,proxy):
    starts[id(rec)]=(time.perf_counter(),actor(),run.get())
@event.listens_for(engine.sync_engine,'checkin')
def checkin(conn,rec):
    item=starts.pop(id(rec),None)
    if item and enabled():
        t,k,r=item
        m['holds'].append({'start':max(t,m['start']),'end':time.perf_counter(),'kind':k,'run_id':r})
@event.listens_for(engine.sync_engine,'before_cursor_execute')
def sql_before(conn,cursor,statement,parameters,context,many):context._bench=(time.perf_counter(),actor(),run.get())
@event.listens_for(engine.sync_engine,'after_cursor_execute')
def sql_after(conn,cursor,statement,parameters,context,many):
    t,k,r=context._bench
    if enabled():m['sql'].append({'start':t,'end':time.perf_counter(),'ms':(time.perf_counter()-t)*1000,'kind':k,'run_id':r,'verb':statement.lstrip().split()[0],'fingerprint':' '.join(statement.split())})

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

module=importlib.import_module('agent_service.agents.analysis.graph')
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
                m['graph_active']-=1;record('graphs',start=start,end=time.perf_counter(),ms=(time.perf_counter()-start)*1000,outcome=outcome)
    graph.ainvoke=wrapped;return graph
module.build_analysis_workflow_graph=measured_build
original_execute=worker.execute_claimed
@wraps(original_execute)
async def execute(item):
    rid=item.claim.run_id
    token=run.set(str(rid));t=time.perf_counter();outcome='ok';m['worker_active']+=1;m['peak_worker']=max(m['peak_worker'],m['worker_active'])
    try:return await original_execute(item)
    except BaseException as exc:outcome=type(exc).__name__;raise
    finally:
        m['worker_active']-=1;record('workers',start=t,end=time.perf_counter(),ms=(time.perf_counter()-t)*1000,outcome=outcome);run.reset(token)
worker.execute_claimed=execute
from api_service.services.run_service import RunService
from api_service.services.llm_token_event_service import LLMTokenEventBuffer
from api_service.services.task_event_service import TaskEventService
for cls,name,label in [(RunService,'_session','run.session'),(RunService,'_lock_run_and_task','run.final_rows'),(LLMTokenEventBuffer,'_append','token.append'),(TaskEventService,'append','event.append')]:
    fn=getattr(cls,name)
    def time_call(fn,label):
        @wraps(fn)
        async def wrapped(*args,**kwargs):
            with diag.span(label):return await fn(*args,**kwargs)
        return wrapped
    setattr(cls,name,time_call(fn,label) if cls is LLMTokenEventBuffer else staticmethod(time_call(fn,label)))
# Measure ChatOpenAI generation boundaries (includes client handling, not wire time alone).
from langchain_openai import ChatOpenAI
orig_sync=ChatOpenAI._generate;orig_async=ChatOpenAI._agenerate
@wraps(orig_sync)
def model_sync(self,*args,**kwargs):
    t=time.perf_counter()
    try:return orig_sync(self,*args,**kwargs)
    finally:record('models',start=t,end=time.perf_counter(),mode='sync',ms=(time.perf_counter()-t)*1000,thread=threading.current_thread().name)
@wraps(orig_async)
async def model_async(self,*args,**kwargs):
    t=time.perf_counter()
    try:return await orig_async(self,*args,**kwargs)
    finally:record('models',start=t,end=time.perf_counter(),mode='async',ms=(time.perf_counter()-t)*1000,thread=threading.current_thread().name)
ChatOpenAI._generate=model_sync;ChatOpenAI._agenerate=model_async

from service_bootstrap import create_app
from service_settings import get_settings
app=create_app(get_settings())

@app.middleware('http')
async def measured(request,call_next):
    route=request.url.path;k='control' if route.startswith('/_bench') else 'run_sse' if route.endswith('/stream') else 'run_get' if '/runs/' in route and request.method=='GET' else 'run_post' if '/runs' in route and request.method=='POST' else 'crud'
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
    from api_service.core.execution_lifecycle import execution_health
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
            from api_service.services.user_service import UserService
            from api_service.schemas.common.user_schema import UserCreate
            async with get_session_factory()() as db:await UserService.bootstrap_admin(db,UserCreate(user_id='admin',user_name='Benchmark admin',role='admin'))
            task=asyncio.create_task(sampler())
            try:yield state
            finally:
                task.cancel();await asyncio.gather(task,return_exceptions=True)
    app.router.lifespan_context=life
    await uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=cfg['port'],log_level='error',timeout_graceful_shutdown=8)).serve()
if __name__ == "__main__":
    asyncio.run(main())
