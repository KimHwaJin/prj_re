"""Loopback-only current HTTP service measurement; no production entry point."""
import argparse, asyncio, json, re, time
from collections import defaultdict
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path

p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);args=p.parse_args()
cfg=json.loads(args.config.read_text())
from service_settings import configure,load_settings
configure(load_settings(config=cfg['settings'],environ={}))
from service_bootstrap import create_app
from api_service.core.database import get_engine
from sqlalchemy import event
from service_runtime import diagnostics as diag
from api_service import agent_run_worker as worker
from model_fixture import install_model_fixture
from service_auth.sso.contracts import VerifiedEmployee

cpu_profiler=None
metrics={};kind=ContextVar('bench_http_kind',default='worker');sql=defaultdict(lambda:[0,0.0,0.0])
def reset():
    sql.clear();metrics.clear();metrics.update(active=True,start=time.perf_counter(),cpu_start=time.process_time(),
        models=[],workers=[],traces=[],acquires=[],http=[],samples=[],loop_lag=[],peak_worker=0,current_worker=0,
        event_handlers=[],event_stages=[],roles=[],current_event_worker=0,peak_event_worker=0,
        current_shared=0,peak_shared=0,claims=0,empty_claims=0,invocations=[],checkpoint_calls=[])
    if cpu_profiler is not None:cpu_profiler.start()
def enabled():return metrics.get('active',False)
if cfg.get('cpu_profile'):
    from cpu_profile import CPUProfile
    cpu_profiler=CPUProfile(enabled)
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

if cfg.get('query_audit'):
    from query_audit import install
    install(engine, metrics, enabled)

original_claim=worker.claim_one
async def claim():
    result=await original_claim()
    if enabled():
        metrics['claims']+=1
        metrics['empty_claims']+=result is None
    return result
worker.claim_one=claim
if cfg.get("executor_trace"):
    import executor_trace
    executor_trace.install(metrics, enabled)
original_execute=worker.execute_claimed
async def execute(item):
    started=time.perf_counter();measured=enabled();user=hasattr(item,'claim')
    if measured:
        metrics['current_shared']+=1
        metrics['peak_shared']=max(metrics['peak_shared'],metrics['current_shared'])
        if user:
            metrics['current_worker']+=1
            metrics['peak_worker']=max(metrics['peak_worker'],metrics['current_worker'])
    trace_token = executor_trace.begin(item) if cfg.get("executor_trace") else None
    try:return await original_execute(item)
    except BaseException as exc:
        # Diagnostic process only: preserve exception cause before ownership
        # quarantine replaces it with a public recovery status. No locals/body.
        if trace_token is not None and measured:
            executor_trace.failed(metrics, exc)
        import traceback
        traceback.print_exception(exc)
        raise
    finally:
        if trace_token is not None:
            executor_trace.command.reset(trace_token)
        if measured:
            ended=time.perf_counter()
            metrics['invocations'].append({'kind':'user' if user else 'event','start':started,'end':ended})
            metrics['current_shared']-=1
            if user:
                metrics['workers'].append({'run_id':str(item.claim.run_id),'start':started,'end':ended})
                metrics['current_worker']-=1
worker.execute_claimed=execute
if cfg.get("checkpoint_profile"):
    from checkpoint_profile import install
    install(metrics, enabled)
    if cfg.get("checkpoint_lock_profile"):
        from checkpoint_lock import install as install_lock
        install_lock()
install_model_fixture(cfg,metrics,enabled)
if cfg.get('executor_probe'):
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'executor_throughput'))
    import probe
    probe.install(metrics,enabled,kind)
# In the baseline the event dispatcher is outside execute_claimed.
from api_service.agent_worker import worker_main
if hasattr(worker_main,'DeferredHandler'):
    previous_event=worker_main.DeferredHandler.__call__
    async def baseline_event(*args):
        started=time.perf_counter();measured=enabled()
        if measured:
            metrics['current_shared']+=1
            metrics['peak_shared']=max(metrics['peak_shared'],metrics['current_shared'])
        try:return await previous_event(*args)
        finally:
            if measured:
                metrics['invocations'].append({'kind':'event','start':started,'end':time.perf_counter()})
                metrics['current_shared']-=1
    worker_main.DeferredHandler.__call__=baseline_event
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
@app.post('/_bench/prepare')
async def prepare():metrics['prepare']=True;return {'ok':True}
@app.get('/_bench/metrics')
async def snapshot():
    profile = cpu_profiler.finish() if cpu_profiler is not None and metrics.get('current_shared') == 0 and engine.sync_engine.pool.checkedout() == 0 else None
    return {**metrics,'crud_owners':[{'category':v['category'],'held_ms':(time.perf_counter()-v['at'])*1000} for v in crud_owners.values()],'psycopg_pools':probe.snapshot() if cfg.get('executor_probe') else [],'crud_connections_checked_out':engine.sync_engine.pool.checkedout(),'cpu_seconds':time.process_time()-metrics['cpu_start'],
        'cpu_profile':profile,'sql':[{'category':k[0],'fingerprint':k[1],'count':v[0],'total_ms':v[1],'max_ms':v[2]} for k,v in sql.items()]}
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
