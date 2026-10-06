"""Loopback-only current HTTP service measurement; no production entry point."""
import argparse, asyncio, json, re, time
from collections import defaultdict
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path

p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);args=p.parse_args()
cfg=json.loads(args.config.read_text())
from dtest.settings.loader import configure,load_settings
configure(load_settings(config=cfg['settings'],environ={}))
from dtest.bootstrap import create_app
from dtest.infrastructure.database.runtime import get_engine
from sqlalchemy import event
from dtest.infrastructure.observability import diagnostics as diag
from dtest.worker_service import command_worker as worker
from dtest.agent_service.agents.analysis.planning.testing import MockConversation
from dtest.contracts.auth import VerifiedEmployee

metrics={};kind=ContextVar('bench_http_kind',default='worker');sql=defaultdict(lambda:[0,0.0,0.0])
def reset():
    sql.clear();metrics.clear();metrics.update(active=True,start=time.perf_counter(),cpu_start=time.process_time(),
        models=[],workers=[],traces=[],acquires=[],http=[],samples=[],loop_lag=[],peak_worker=0,current_worker=0,
        event_handlers=[],event_stages=[],roles=[],current_event_worker=0,peak_event_worker=0)
def enabled():return metrics.get('active',False)
def category():
    name=asyncio.current_task().get_name()
    if name.startswith('cancel-watch:'):return 'cancel_watch'
    if name.startswith('agent-run-claim'):return 'claim'
    return kind.get()
# Collect existing timings in memory; avoid synchronous trace file writes.
def emit(self,event,**fields):
    if enabled() and event=='run_end':metrics['traces'].append({'run_id':self.run_id,**fields})
diag.Trace.emit=emit
engine=get_engine()
# Explicit diagnostic A/B only. A production setting is added only after evidence.
if cfg.get('prepared_cache_probe') is not None:
    raise RuntimeError('Configure the production database cache field, do not patch engine internals')
@event.listens_for(engine.sync_engine,'before_cursor_execute')
def before(conn,cursor,statement,parameters,context,many):context._bench=(time.perf_counter(),category())
@event.listens_for(engine.sync_engine,'after_cursor_execute')
def after(conn,cursor,statement,parameters,context,many):
    if not enabled():return
    start,cat=context._bench;ms=(time.perf_counter()-start)*1000
    fingerprint=' '.join(statement.split())
    row=sql[(cat,fingerprint)];row[0]+=1;row[1]+=ms;row[2]=max(row[2],ms)
old_get=engine.sync_engine.pool._do_get
def get():
    started=time.perf_counter()
    try:return old_get()
    finally:
        if enabled():metrics['acquires'].append((time.perf_counter()-started)*1000)
engine.sync_engine.pool._do_get=get
# Optional hold diagnostic, disabled for the matched performance matrices.
crud_owners={}
if cfg.get('hold_owner_probe'):
    @event.listens_for(engine.sync_engine.pool,'checkout')
    def track_checkout(connection,record,proxy):
        crud_owners[id(record)]={'category':category(),'at':time.perf_counter()}
    @event.listens_for(engine.sync_engine.pool,'checkin')
    def track_checkin(connection,record):
        crud_owners.pop(id(record),None)

original_execute=worker.execute_claimed
async def execute(item):
    # Event calls are measured by the Executor probe. This list is private
    # user invocations only, in both the old and common-worker architectures.
    if not hasattr(item, 'claim'):
        return await original_execute(item)
    started=time.perf_counter();measured=enabled()
    if measured:
        metrics['current_worker']+=1;metrics['peak_worker']=max(metrics['peak_worker'],metrics['current_worker'])
    try:return await original_execute(item)
    finally:
        if measured:
            metrics['workers'].append({'run_id':str(item.claim.run_id),'start':started,'end':time.perf_counter()})
            metrics['current_worker']-=1
worker.execute_claimed=execute
original_model=MockConversation.ainvoke
async def model(self,*a,**kw):
    started=time.perf_counter();trace=diag._current.get()
    try:return await original_model(self,*a,**kw)
    finally:
        if enabled():metrics['models'].append({'run_id':trace.run_id if trace else None,'start':started,'end':time.perf_counter()})
MockConversation.ainvoke=model
if cfg.get('executor_probe'):
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'executor_throughput'))
    import probe
    probe.install(metrics,enabled,kind)
app=create_app()
class EmployeeFixture:
    async def verify(self,request):
        employee=request.query_params.get('employee','')
        if not re.fullmatch(re.escape(cfg['namespace'])+r'-[0-9]+',employee):raise ValueError('Explicit diagnostic employee required')
        return VerifiedEmployee(employee,'Throughput fixture')
    async def login_url(self,*args):raise AssertionError('No corporate SSO browser roundtrip')
assert not app.dependency_overrides
app.state.sso.adapter=EmployeeFixture()
class MeasureMiddleware:
    # Pure ASGI: never introduce BaseHTTPMiddleware stream cancellation scopes.
    def __init__(self, app):self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']!='http':return await self.app(scope,receive,send)
        path=scope['path'];cat='control' if path.startswith('/_bench') else 'sse' if path.endswith('/stream') else 'get' if scope['method']=='GET' else 'write'
        token=kind.set(cat);started=time.perf_counter()
        async def measured_send(message):
            if message['type']=='http.response.start' and enabled() and cat!='control':
                metrics['http'].append({'kind':cat,'ms':(time.perf_counter()-started)*1000,'status':message['status']})
            await send(message)
        try:return await self.app(scope,receive,measured_send)
        finally:kind.reset(token)
app.add_middleware(MeasureMiddleware)
@app.get('/_bench/ready')
async def ready():return {'ready':True}
@app.post('/_bench/reset')
async def clear():reset();return {'ok':True}
@app.get('/_bench/metrics')
async def snapshot():
    return {**metrics,'crud_owners':[{'category':v['category'],'held_ms':(time.perf_counter()-v['at'])*1000} for v in crud_owners.values()],'psycopg_pools':probe.snapshot() if cfg.get('executor_probe') else [],'crud_connections_checked_out':engine.sync_engine.pool.checkedout(),'cpu_seconds':time.process_time()-metrics['cpu_start'],
        'sql':[{'category':k[0],'fingerprint':k[1],'count':v[0],'total_ms':v[1],'max_ms':v[2]} for k,v in sql.items()]}
original_lifespan=app.router.lifespan_context
@asynccontextmanager
async def lifespan(app):
    async with original_lifespan(app):
        async def sample():
            previous=time.perf_counter()
            while True:
                await asyncio.sleep(.1);now=time.perf_counter()
                if enabled():metrics['loop_lag'].append(max(0,(now-previous-.1)*1000))
                previous=now
        task=asyncio.create_task(sample())
        try:yield
        finally:
            task.cancel();await asyncio.gather(task,return_exceptions=True)
app.router.lifespan_context=lifespan
if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=cfg['port'],log_level='error')
