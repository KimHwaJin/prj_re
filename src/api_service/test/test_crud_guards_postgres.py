"""Real PostgreSQL/HTTP lifecycle guards and controlled admission races.

Only the guarded disposable identity_test DB is used. No LLM, Redis or Executor.
"""
import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select, text, update, func

import api_service.agent_run_worker as worker
import api_service.runs.execution as runs
from api_service.core.enums import AgentRunStatus, DeleteYN, LLMRunStatus, MessageType, TaskStatus
from api_service.models.common.agent_run_model import AgentRunModel as Run
from api_service.models.common.llm_run_model import LLMRunModel as LLM
from api_service.models.common.message_model import MessageModel as Message
from api_service.models.common.project_model import ProjectModel as Project
from api_service.models.common.session_model import SessionModel as Session
from api_service.models.common.session_execution_model import SessionExecutionModel as Owner
from api_service.models.common.task_model import TaskModel as Task
from api_service.services import resource_lifecycle as lifecycle
from api_service.services.session_execution import run_event_owned
from api_service.services.session_service import SessionService
from api_service.services.task_event_service import TaskEventService
from api_service.worker import DeferEvent
from api_service.test.test_user_identity_postgres import database_url, harness, headers, add_session
from api_service.test.test_run_cleanup_postgres import runtime, enqueue


async def new_project(h):
    result=await h.client.post('/api/v1/projects',headers=headers(h.user['user_id']),json={'project_name':'project-'+uuid4().hex})
    assert result.status_code==201,result.text
    return result.json()['id']


async def new_session(h, project):
    return await h.client.post(f'/api/v1/projects/{project}/sessions',headers=headers(h.user['user_id']),json={'session_name':'test'})


@pytest_asyncio.fixture
async def resources(runtime):
    h=runtime
    h.project_id=await new_project(h)
    h.target_id=await new_project(h)
    h.session_id=(await new_session(h,h.project_id)).json()['id']
    async with h.factory() as db:
        h.internal_user=(await db.get(Session,UUID(h.session_id))).user_id
        message=Message(session_id=UUID(h.session_id),message_type=MessageType.USER,content_text='preserve')
        db.add(message); await db.commit()
        h.message_id=message.message_id
    return h


async def seed(h, case):
    sid=UUID(h.session_id)
    async with h.factory() as db:
        if case.startswith('orphan_'):
            db.add(Run(session_id=sid,status=AgentRunStatus(case.removeprefix('orphan_')),idempotency_key=case))
        elif case.startswith('llm_'):
            db.add(LLM(session_id=sid,trigger_message_id=h.message_id,provider='test',model_name='test',
                       project_prompt_version=1,system_prompt_snapshot='',request_messages_snapshot=[],
                       status=LLMRunStatus(case.removeprefix('llm_'))))
        elif case.startswith('owner_'):
            db.add(Owner(session_id=sid,token=uuid4() if case=='owner_live' else None,
                         recovery_required=case=='owner_recovery'))
        else:
            status={'waiting_executor':'waiting_input','recovery':'error','finished_history':'success'}.get(case,case)
            task=Task(session_id=sid,status=TaskStatus(status),idempotency_key=case,recovery_required=case=='recovery')
            db.add(task); await db.flush()
            run=Run(session_id=sid,task_id=task.task_id,status=AgentRunStatus.INTERRUPTED,idempotency_key=case,
                    interrupt=[{'kind':'EXECUTOR_EVENT' if case=='waiting_executor' else 'USER_APPROVAL'}])
            db.add(run); await db.flush()
            task.root_run_id=task.checkpoint_run_id=run.run_id
        await db.commit()


async def mutate(h, operation):
    if operation=='session_delete':
        return await h.client.delete(f'/api/v1/sessions/{h.session_id}',headers=headers(h.user['user_id']))
    if operation=='project_delete':
        return await h.client.delete(f'/api/v1/projects/{h.project_id}',headers=headers(h.user['user_id']))
    if operation=='move':
        return await h.client.patch(f'/api/v1/sessions/{h.session_id}',headers=headers(h.user['user_id']),
                                    json={'target_project_id':h.target_id,'session_name':'must not partially apply'})
    if operation=='user_delete':
        return await h.client.delete(f"/api/v1/users/{h.user['user_id']}",headers=headers('admin'))
    raise AssertionError(operation)


@pytest.mark.asyncio
@pytest.mark.parametrize('case',['pending','running','waiting_input','waiting_executor','recovery',
    'owner_live','owner_recovery','orphan_pending','orphan_running','orphan_interrupted','llm_queued','llm_running'])
@pytest.mark.parametrize('operation',['session_delete','project_delete','move','user_delete'])
async def test_unfinished_states_reject_without_mutation(resources,case,operation):
    h=resources
    await seed(h,case)
    response=await mutate(h,operation)
    assert response.status_code==409,response.text
    async with h.factory() as db:
        s=await db.get(Session,UUID(h.session_id)); p=await db.get(Project,UUID(h.project_id)); m=await db.get(Message,h.message_id)
        assert s.delete_yn==p.delete_yn==m.delete_yn==DeleteYN.N
        assert str(s.project_id)==h.project_id and s.session_name=='test'


@pytest.mark.asyncio
@pytest.mark.parametrize('operation',['session_delete','project_delete','move','user_delete'])
async def test_finished_history_and_released_owner_allow_mutation(resources,operation):
    h=resources
    await seed(h,'finished_history'); await seed(h,'owner_released')
    result=await mutate(h,operation)
    assert result.status_code==(200 if operation=='move' else 204),result.text
    async with h.factory() as db:
        s=await db.get(Session,UUID(h.session_id)); m=await db.get(Message,h.message_id)
        if operation=='move':
            assert str(s.project_id)==h.target_id and s.delete_yn==m.delete_yn==DeleteYN.N
        else:
            assert s.delete_yn==m.delete_yn==DeleteYN.Y


@pytest.mark.asyncio
@pytest.mark.parametrize('case',['running','waiting_executor','recovery'])
async def test_names_and_same_project_noop_remain_editable(resources,case):
    h=resources; await seed(h,case)
    res=await h.client.patch(f'/api/v1/sessions/{h.session_id}',headers=headers(h.user['user_id']),
                             json={'session_name':'renamed','target_project_id':h.project_id})
    assert res.status_code==200 and res.json()['name']=='renamed',res.text
    res=await h.client.patch(f'/api/v1/projects/{h.project_id}',headers=headers(h.user['user_id']),json={'project_name':'renamed'})
    assert res.status_code==200,res.text


@pytest.mark.asyncio
async def test_default_delete_is_not_conversation_clear_and_ownership_stays_hidden(resources):
    h=resources
    sid=await add_session(h,h.user)
    res=await h.client.delete(f"/api/v1/projects/{h.user['default_project_id']}",headers=headers(h.user['user_id']))
    assert res.status_code==409
    assert (await h.client.get(f'/api/v1/sessions/{sid}',headers=headers(h.user['user_id']))).status_code==200
    for path in [f'/api/v1/projects/{h.project_id}',f'/api/v1/sessions/{h.session_id}']:
        assert (await h.client.delete(path,headers=headers('admin'))).status_code==404


async def blocked_in_postgres(h):
    """Prove the second transaction is waiting on a real PG advisory barrier."""
    async with asyncio.timeout(5):
        while True:
            async with h.factory() as db:
                count=await db.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND wait_event='advisory' AND cardinality(pg_blocking_pids(pid))>0"))
            if count:
                return
            await asyncio.sleep(.01)


def hold_idle(monkeypatch, resource):
    entered,release=asyncio.Event(),asyncio.Event()
    original=lifecycle.require_idle
    async def wrapped(db,ids,**kwargs):
        if kwargs['resource']==resource:
            entered.set(); await release.wait()
        return await original(db,ids,**kwargs)
    monkeypatch.setattr(lifecycle,'require_idle',wrapped)
    return entered,release


async def post_run(h):
    return await h.client.post(f'/api/v1/sessions/{h.session_id}/runs',headers={**headers(h.user['user_id']),'Idempotency-Key':str(uuid4())},
                              json={'input':{'content': [{'type': 'text', 'text': 'race'}]}})


@pytest.mark.asyncio
@pytest.mark.parametrize('operation',['session_delete','project_delete','move'])
async def test_mutation_wins_then_stale_run_admission_is_rejected(resources,monkeypatch,operation):
    h=resources
    entered,release=hold_idle(monkeypatch,'Project' if operation=='project_delete' else 'Session')
    first=asyncio.create_task(mutate(h,operation))
    second=None
    try:
        await asyncio.wait_for(entered.wait(),5)
        second=asyncio.create_task(post_run(h))
        await blocked_in_postgres(h)
    finally:
        release.set()
    result,admitted=await asyncio.wait_for(asyncio.gather(first,second),5)
    assert result.status_code==(200 if operation=='move' else 204),result.text
    assert admitted.status_code in (404,409),admitted.text
    async with h.factory() as db:
        assert await db.scalar(select(func.count()).select_from(Run).where(Run.session_id==UUID(h.session_id)))==0


@pytest.mark.asyncio
@pytest.mark.parametrize('operation',['session_delete','project_delete','move'])
async def test_admission_wins_then_mutation_observes_committed_task(resources,monkeypatch,operation):
    h=resources
    entered,release=asyncio.Event(),asyncio.Event()
    original=TaskEventService.append
    async def held(*args,**kwargs):
        event=await original(*args,**kwargs)
        if kwargs.get('event_type')=='task.queued':
            entered.set(); await release.wait()
        return event
    monkeypatch.setattr(TaskEventService,'append',held)
    first=asyncio.create_task(post_run(h)); second=None
    try:
        await asyncio.wait_for(entered.wait(),5)
        second=asyncio.create_task(mutate(h,operation))
        await blocked_in_postgres(h)
    finally:
        release.set()
    admitted,result=await asyncio.wait_for(asyncio.gather(first,second),5)
    assert admitted.status_code==202,admitted.text
    assert result.status_code==409,result.text


@pytest.mark.asyncio
async def test_project_delete_blocks_new_session_and_move_into_it(resources,monkeypatch):
    h=resources
    # Delete the target project while a create and an incoming move are queued.
    entered,release=hold_idle(monkeypatch,'Project')
    deleting=asyncio.create_task(h.client.delete(f'/api/v1/projects/{h.target_id}',headers=headers(h.user['user_id'])))
    pending=[]
    try:
        await asyncio.wait_for(entered.wait(),5)
        pending=[asyncio.create_task(new_session(h,h.target_id)),asyncio.create_task(mutate(h,'move'))]
        await blocked_in_postgres(h)
    finally:
        release.set()
    results=await asyncio.wait_for(asyncio.gather(deleting,*pending),5)
    assert [r.status_code for r in results]==[204,404,404],[r.text for r in results]
    async with h.factory() as db:
        assert str((await db.get(Session,UUID(h.session_id))).project_id)==h.project_id
        assert not list(await db.scalars(select(Session.session_id).where(Session.project_id==UUID(h.target_id),Session.delete_yn==DeleteYN.N)))


@pytest.mark.asyncio
async def test_new_session_commits_before_project_delete_enumerates_children(resources,monkeypatch):
    h=resources
    entered,release=asyncio.Event(),asyncio.Event()
    original=SessionService.create_internal
    async def held(*args,**kwargs):
        session=await original(*args,**kwargs)
        entered.set(); await release.wait()
        return session
    monkeypatch.setattr(SessionService,'create_internal',held)
    creating=asyncio.create_task(new_session(h,h.project_id)); deleting=None
    try:
        await asyncio.wait_for(entered.wait(),5)
        deleting=asyncio.create_task(mutate(h,'project_delete'))
        await blocked_in_postgres(h)
    finally:
        release.set()
    created,deleted=await asyncio.wait_for(asyncio.gather(creating,deleting),5)
    assert created.status_code==201 and deleted.status_code==204
    async with h.factory() as db:
        assert (await db.get(Session,UUID(created.json()['id']))).delete_yn==DeleteYN.Y


@pytest.mark.asyncio
async def test_opposite_moves_and_different_session_admission_do_not_serialize(resources):
    h=resources
    other=(await new_session(h,h.target_id)).json()['id']
    results=await asyncio.wait_for(asyncio.gather(mutate(h,'move'),h.client.patch(f'/api/v1/sessions/{other}',
        headers=headers(h.user['user_id']),json={'target_project_id':h.project_id})),5)
    assert all(r.status_code==200 for r in results),[r.text for r in results]
    same_project=(await new_session(h,h.project_id)).json()['id']
    async with h.factory() as held:
        await lifecycle.lock_session(held,h.internal_user,UUID(other))
        # Shared project barrier must not serialize independent sessions.
        response=await asyncio.wait_for(h.client.post(f'/api/v1/sessions/{same_project}/runs',
            headers={**headers(h.user['user_id']),'Idempotency-Key':'independent'},
            json={'input':{'content': [{'type': 'text', 'text': 'independent'}]}}),3)
        assert response.status_code==202,response.text


@pytest.mark.asyncio
async def test_project_checks_unfinished_work_in_legacy_hidden_session(resources):
    h=resources; await seed(h,'waiting_executor')
    async with h.factory() as db:
        await db.execute(update(Session).where(Session.session_id==UUID(h.session_id)).values(delete_yn=DeleteYN.Y))
        await db.commit()
    assert (await mutate(h,'project_delete')).status_code==409


@pytest.mark.asyncio
async def test_event_owner_blocks_delete_until_operation_finishes(resources):
    h=resources
    entered,release=asyncio.Event(),asyncio.Event()
    async def operation():
        entered.set(); await release.wait()
    event=asyncio.create_task(run_event_owned(SimpleNamespace(session_id=h.session_id,command_id=uuid4()),operation))
    try:
        await asyncio.wait_for(entered.wait(),5)
        # No Task at all: independent Executor owner is enough to protect data.
        for operation_name in ('session_delete','project_delete','move','user_delete'):
            assert (await mutate(h,operation_name)).status_code==409
    finally:
        release.set(); await asyncio.wait_for(event,5)
    assert (await mutate(h,'session_delete')).status_code==204


@pytest.mark.asyncio
async def test_deleted_resources_reject_late_event_before_graph_call(resources,monkeypatch):
    h=resources
    entered,release=hold_idle(monkeypatch,'Session')
    deleting=asyncio.create_task(mutate(h,'session_delete'))
    called=AsyncMock(); event=None
    try:
        await asyncio.wait_for(entered.wait(),5)
        event=asyncio.create_task(run_event_owned(SimpleNamespace(session_id=h.session_id,command_id=uuid4()),called))
        await blocked_in_postgres(h)
    finally:
        release.set()
    assert (await deleting).status_code==204
    with pytest.raises(DeferEvent):
        await asyncio.wait_for(event,5)
    called.assert_not_awaited()


@pytest.mark.asyncio
async def test_running_worker_allows_rename_but_not_move_or_delete(resources,monkeypatch):
    h=resources
    entered,release=asyncio.Event(),asyncio.Event()
    async def graph(**kwargs):
        assert str(kwargs['project_id'])==h.project_id
        entered.set(); await release.wait()
        return {'routing_result':{'route':'analysis'}}
    monkeypatch.setattr(runs,'ainvoke_user_turn',graph)
    await enqueue(h)
    job=asyncio.create_task(worker.execute_claimed(await worker.claim_one()))
    try:
        await asyncio.wait_for(entered.wait(),5)
        for operation in ('move','session_delete','project_delete'):
            assert (await mutate(h,operation)).status_code==409
        renamed=await h.client.patch(f'/api/v1/sessions/{h.session_id}',headers=headers(h.user['user_id']),json={'session_name':'during inference'})
        assert renamed.status_code==200
    finally:
        release.set(); await asyncio.wait_for(job,5)
    assert (await mutate(h,'move')).status_code==200


@pytest.mark.asyncio
@pytest.mark.parametrize('action',['create','update'])
async def test_public_message_write_cannot_race_past_parent_delete(resources,monkeypatch,action):
    h=resources
    entered,release=hold_idle(monkeypatch,'Project')
    deleting=asyncio.create_task(mutate(h,'project_delete')); writing=None
    try:
        await asyncio.wait_for(entered.wait(),5)
        if action=='create':
            writing=asyncio.create_task(h.client.post('/api/v1/messages',headers=headers(h.user['user_id']),
                json={'session_id':h.session_id,'content_text':'late insert'}))
        else:
            writing=asyncio.create_task(h.client.patch(f'/api/v1/messages/{h.message_id}',headers=headers(h.user['user_id']),
                json={'content_text':'late update'}))
        await blocked_in_postgres(h)
    finally:
        release.set()
    deleted,written=await asyncio.wait_for(asyncio.gather(deleting,writing),5)
    assert deleted.status_code==204 and written.status_code==404,[deleted.text,written.text]
    async with h.factory() as db:
        assert not list(await db.scalars(select(Message.message_id).where(Message.session_id==UUID(h.session_id),Message.delete_yn==DeleteYN.N)))
        assert (await db.get(Message,h.message_id)).content_text=='preserve'


@pytest.mark.asyncio
async def test_resume_admission_holds_movement_barrier(resources,monkeypatch):
    h=resources
    monkeypatch.setattr(runs,'ainvoke_user_turn',AsyncMock(return_value={
        'routing_result':{'route':'analysis'},'__interrupt__':[SimpleNamespace(value={'kind':'USER_APPROVAL'})]}))
    first=await enqueue(h)
    await worker.execute_claimed(await worker.claim_one())
    endpoint=f"/api/v1/sessions/{h.session_id}/runs/{first['run_id']}"
    current=(await h.client.get(endpoint,headers=headers(h.user['user_id']))).json()
    entered,release=asyncio.Event(),asyncio.Event()
    original=TaskEventService.append
    async def held(*args,**kwargs):
        result=await original(*args,**kwargs)
        if kwargs.get('event_type')=='task.queued':
            entered.set(); await release.wait()
        return result
    monkeypatch.setattr(TaskEventService,'append',held)
    resuming=asyncio.create_task(h.client.post(f'/api/v1/sessions/{h.session_id}/runs',headers={**headers(h.user['user_id']),'Idempotency-Key':'resume'},
        json={'run_id':first['run_id'], 'resume_token':current['resume_token'],'command':{'resume':{'action':'approve_plan','plan_id':'test-plan','plan_revision':1}}}))
    moving=None
    try:
        await asyncio.wait_for(entered.wait(),5)
        moving=asyncio.create_task(mutate(h,'move'))
        await blocked_in_postgres(h)
    finally:
        release.set()
    resumed,moved=await asyncio.wait_for(asyncio.gather(resuming,moving),5)
    assert resumed.status_code==202 and resumed.json()['run_id']==first['run_id'],resumed.text
    assert moved.status_code==409,moved.text


@pytest.mark.asyncio
async def test_move_out_commits_before_source_project_delete_enumerates(resources,monkeypatch):
    h=resources
    entered,release=hold_idle(monkeypatch,'Session')
    moving=asyncio.create_task(mutate(h,'move')); deleting=None
    try:
        await asyncio.wait_for(entered.wait(),5)
        deleting=asyncio.create_task(mutate(h,'project_delete'))
        await blocked_in_postgres(h)
    finally:
        release.set()
    moved,deleted=await asyncio.wait_for(asyncio.gather(moving,deleting),5)
    assert moved.status_code==200 and deleted.status_code==204
    async with h.factory() as db:
        session=await db.get(Session,UUID(h.session_id))
        assert session.delete_yn==DeleteYN.N and str(session.project_id)==h.target_id
        assert (await db.get(Message,h.message_id)).delete_yn==DeleteYN.N
