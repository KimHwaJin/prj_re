"""In-memory diagnostic hooks for actual Event Worker/pool/result integration."""
import asyncio, time
from contextvars import ContextVar

pools=[]

def install(metrics,enabled,kind):
    from psycopg_pool import AsyncConnectionPool
    old_init=AsyncConnectionPool.__init__
    def init(self,*a,**kw):
        old_init(self,*a,**kw);pools.append(self)
    AsyncConnectionPool.__init__=init
    from api_service.agent_worker.worker_main import DeferredHandler
    original=DeferredHandler.__call__
    async def handle(self,context):
        start=time.perf_counter();measured=enabled();token=kind.set('event_graph');error=None
        if measured:
            metrics['current_event_worker']+=1
            metrics['peak_event_worker']=max(metrics['peak_event_worker'],metrics['current_event_worker'])
        try:return await original(self,context)
        except BaseException as exc:error=type(exc).__name__;raise
        finally:
            if measured:
                metrics['event_handlers'].append({'command_id':str(context.command_id),'event_id':str(context.event.event_id),
                    'execution_id':str(context.execution_id),'session_id':context.session_id,'event_type':context.event.event_type,
                    'start':start,'end':time.perf_counter(),'error':error})
                metrics['current_event_worker']-=1
            kind.reset(token)
    DeferredHandler.__call__=handle
    from api_service.worker.store import Store
    def wrap(name):
        prior=getattr(Store,name)
        async def invoke(self,*args,**kw):
            start=time.perf_counter()
            try:return await prior(self,*args,**kw)
            finally:
                if enabled():
                    row={'method':name,'start':start,'end':time.perf_counter()}
                    if name=='ingest':row.update(event_id=str(args[0].event_id),execution_id=str(args[0].execution_id),event_type=args[0].event_type)
                    elif name=='advance':row['execution_id']=str(args[0])
                    elif name=='set_state':row.update(command_id=str(args[0]),state=args[1])
                    elif name=='finish_publications':row.update(command_ids=[str(c) for c in args[1]],sent=kw['sent'])
                    metrics['event_stages'].append(row)
        setattr(Store,name,invoke)
    for name in ('ingest','advance','finish_publications','set_state'):wrap(name)
    from agent_service.agents.analysis.planning.runtime import PlanningRuntime
    old_role=PlanningRuntime.execution_role
    async def role(self,name,state,payload):
        start=time.perf_counter()
        try:return await old_role(self,name,state,payload)
        finally:
            if enabled():metrics['roles'].append({'role':name,'run_id':state.get('agent_run_id'),
                'execution_id':state.get('execution_id'),'start':start,'end':time.perf_counter()})
    PlanningRuntime.execution_role=role


def snapshot():
    return [{'index':i,'max_size':pool.max_size,'closed':pool.closed,**pool.get_stats()} for i,pool in enumerate(pools)]
