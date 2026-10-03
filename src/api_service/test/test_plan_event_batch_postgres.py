"""Current public-event projection: one barrier per owner, rollback and replay."""
from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID,uuid4

import pytest
from sqlalchemy import event,select,func
from api_service.services.agent_run_log_service import AgentRunLogService
from api_service.services.plan_event_persistence import persist_plan_events
from api_service.models.common.agent_run_log_model import AgentRunLogModel
from api_service.models.common.task_event_model import TaskEventModel
from api_service.models.common.task_model import TaskModel
# Reuse the guarded real PostgreSQL/Worker/checkpoint fixture, never a live DB.
from api_service.test.test_planning_api_postgres import test_config,planning,submit,execute,read

@pytest.mark.asyncio
async def test_public_event_batch_barrier_replay_and_partial_failure(planning,monkeypatch):
    h=planning
    accepted=await submit(h,{'input':{'content':[{'type':'text','text':'품질 확인'}]}})
    assert accepted.status_code==202
    await execute();waiting=await read(h,accepted.json()['run_id'])
    owner=UUID(waiting['resume_token']);task_id=UUID(waiting['task_id'])
    async with h.factory() as db:
        user=await db.scalar(select(TaskModel).where(TaskModel.task_id==task_id))
        session_id=user.session_id
    from api_service.models.common.session_model import SessionModel
    async with h.factory() as db:
        session=await db.get(SessionModel,session_id)
        context=SimpleNamespace(user_id=session.user_id,session_id=str(session_id))
    def make_events():
        return [{'event_id':str(uuid4()),'owner_run_id':str(owner),'envelope':{
            'schema_version':1,'type':'activity.updated','sequence':1,
            'session_id':str(session_id),'run_id':accepted.json()['run_id'],
            'occurred_at':'2026-10-03T00:00:00+00:00','data':{'title':'Projection probe'}}} for _ in range(3)]
    events=make_events();state={'public_events':events,'project_id':str(session.project_id)}
    statements=[]
    engine=h.factory.kw['bind']
    def observe(conn,cursor,statement,parameters,context,many):statements.append(statement)
    event.listen(engine.sync_engine,'before_cursor_execute',observe)
    try:
        async with h.factory() as db:await persist_plan_events(db,state,context)
    finally:event.remove(engine.sync_engine,'before_cursor_execute',observe)
    assert sum('pg_advisory_xact_lock' in statement for statement in statements)==1
    keys=['public:'+item['event_id'] for item in events]
    async def snapshot():
        async with h.factory() as db:
            logs=list(await db.scalars(select(AgentRunLogModel).where(AgentRunLogModel.event_key.in_(keys))))
            linked=list(await db.scalars(select(TaskEventModel).where(TaskEventModel.agent_run_log_id.in_([log.log_id for log in logs]))))
            task=await db.get(TaskModel,task_id)
            return {log.log_id for log in logs},{item.task_event_id for item in linked},task.last_event_sequence
    before=await snapshot();assert len(before[0])==len(before[1])==3
    async with h.factory() as db:await persist_plan_events(db,deepcopy(state),context)
    assert await snapshot()==before
    failed=make_events();original=AgentRunLogService.create;calls=0
    async def fail_second(*args,**kwargs):
        nonlocal calls
        calls+=1
        if calls==2:raise RuntimeError('Injected transaction failure')
        return await original(*args,**kwargs)
    monkeypatch.setattr(AgentRunLogService,'create',staticmethod(fail_second))
    async with h.factory() as db:
        with pytest.raises(RuntimeError,match='Injected transaction failure'):
            await persist_plan_events(db,{'public_events':failed},context)
    async with h.factory() as db:
        assert await db.scalar(select(func.count()).select_from(AgentRunLogModel).where(AgentRunLogModel.event_key.in_(['public:'+item['event_id'] for item in failed])))==0
        assert (await db.get(TaskModel,task_id)).last_event_sequence==before[2]
    monkeypatch.setattr(AgentRunLogService,'create',staticmethod(original))
    async with h.factory() as db:await persist_plan_events(db,{'public_events':failed},context)
    async with h.factory() as db:
        assert (await db.get(TaskModel,task_id)).last_event_sequence==before[2]+3


@pytest.mark.asyncio
async def test_invocation_projection_commits_deltas_and_new_invocation_repairs_replay(planning):
    from api_service.services.graph_crud_persistence import InvocationProjection
    h=planning
    accepted=await submit(h,{'input':{'content':[{'type':'text','text':'품질 확인'}]}})
    await execute();waiting=await read(h,accepted.json()['run_id'])
    owner=UUID(waiting['resume_token']);task_id=UUID(waiting['task_id'])
    from api_service.models.common.session_model import SessionModel
    async with h.factory() as db:
        task=await db.get(TaskModel,task_id);session=await db.get(SessionModel,task.session_id)
    def public():
        return {'event_id':str(uuid4()),'owner_run_id':str(owner),'envelope':{'schema_version':1,
            'type':'activity.updated','sequence':1,'session_id':str(session.session_id),
            'run_id':accepted.json()['run_id'],'occurred_at':'2026-10-03T00:00:00+00:00','data':{'title':'Projection probe'}}}
    first,second=public(),public()
    state={'agent_runtime':'agentic-planning-v1','public_events':[first],
        'task_id':str(task.graph_task_id),'session_id':str(session.session_id),'project_id':str(session.project_id)}
    projection=InvocationProjection();kw={'user_id':session.user_id,'agent_run_id':owner}
    await projection.persist(state,**kw)
    async def snapshot():
        async with h.factory() as db:
            logs=list(await db.scalars(select(AgentRunLogModel).where(AgentRunLogModel.event_key.in_(['public:'+first['event_id'],'public:'+second['event_id']]))))
            task=await db.get(TaskModel,task_id)
            return {log.log_id for log in logs},task.last_event_sequence
    initial=await snapshot()
    statements=[];engine=h.factory.kw['bind']
    def observe(conn,cursor,statement,parameters,context,many):statements.append(statement)
    event.listen(engine.sync_engine,'before_cursor_execute',observe)
    try:await projection.persist(deepcopy(state),**kw)
    finally:event.remove(engine.sync_engine,'before_cursor_execute',observe)
    assert statements==[] and await snapshot()==initial
    state['public_events'].append(second);await projection.persist(state,**kw)
    updated=await snapshot();assert len(updated[0])==2 and updated[1]==initial[1]+1
    await InvocationProjection().persist(deepcopy(state),**kw)
    assert await snapshot()==updated
