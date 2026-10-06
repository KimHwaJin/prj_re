"""Canonical Run identity, legacy history and read-only diagnostic boundaries."""
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text

from api_service.models.enums import AgentRunStatus, DeleteYN, TaskStatus
from api_service.api.pagination import ListParams
from api_service.models.agent_run_model import AgentRunModel as Run
from api_service.models.session_model import SessionModel as Session
from api_service.models.task_model import TaskModel as Task
from tests.api_service.test_user_identity_postgres import database_url, harness, headers, add_session
from tests.api_service.test_run_cleanup_postgres import runtime
from tests.api_service.test_crud_guards_postgres import resources, seed
from tests.api_service.test_run_diagnostics_postgres import many_tasks
from api_service.runs import diagnostics as run_diagnostics


def path(h, rid, suffix, *, admin=False):
    return '/api/v1' + ('/admin' if admin else '') + f'/sessions/{h.session_id}/runs/{rid}/{suffix}'


@pytest.mark.asyncio
async def test_taskless_legacy_run_and_alias_return_same_public_history(resources):
    h = resources
    root, alias = uuid4(), uuid4()
    stamp = datetime(2026,1,1,tzinfo=timezone.utc)
    async with h.factory() as db:
        for i, rid in enumerate((root, alias)):
            db.add(Run(run_id=rid,public_run_id=root,session_id=UUID(h.session_id),
                status=AgentRunStatus.SUCCESS if i else AgentRunStatus.INTERRUPTED,
                created_at=stamp+timedelta(seconds=i),idempotency_key=str(rid)))
        await db.commit()
    for admin in (False,True):
        hdr=headers('admin' if admin else h.user['user_id'])
        for rid in (root,alias):
            response = await h.client.get(path(h,rid,'diagnostics',admin=admin), headers=hdr)
            assert response.status_code == 200, response.text
            body = response.json()
            assert body['run_id'] == str(root) and body['task'] is None
            assert body['session_work']['can_start_new_run'] and not body['session_work']['blocking_reasons']
            response = await h.client.get(path(h,rid,'invocations',admin=admin), headers=hdr)
            assert response.status_code == 200, response.text
            items = response.json()['items']
            assert {item['invocation_id'] for item in items} == {str(root),str(alias)}
            assert {item['run_id'] for item in items} == {str(root)}
            assert all(item['task_id'] is None and 'public_run_id' not in item for item in items)


@pytest.mark.asyncio
async def test_latest_task_selected_while_all_public_invocations_remain_visible(resources):
    h = resources
    tids, histories = await many_tasks(h,count=1,invocation_count=1)
    root, alias = histories[0][0], uuid4()
    async with h.factory() as db:
        task = Task(session_id=UUID(h.session_id),root_run_id=root,status=TaskStatus.SUCCESS,idempotency_key='legacy-new-task')
        db.add(task); await db.flush()
        newest_tid = task.task_id
        db.add(Run(run_id=alias,public_run_id=root,session_id=UUID(h.session_id),task_id=newest_tid,
            status=AgentRunStatus.SUCCESS,created_at=datetime(2026,1,2,tzinfo=timezone.utc),idempotency_key='legacy-resume'))
        await db.commit()
    for rid in (root,alias):
        response = await h.client.get(path(h,rid,'diagnostics'),headers=headers(h.user['user_id']))
        assert response.status_code == 200, response.text
        assert response.json()['run_id'] == str(root)
        assert response.json()['task']['task_id'] == str(newest_tid)
        body = (await h.client.get(path(h,rid,'invocations'),headers=headers(h.user['user_id']))).json()
        assert len(body['items']) == 2
        assert {item['task_id'] for item in body['items']} == {str(tids[0]),str(newest_tid)}


@pytest.mark.asyncio
async def test_malformed_cross_session_alias_and_task_cannot_disclose_foreign_state(resources):
    h = resources
    tids, histories = await many_tasks(h,count=1,invocation_count=1)
    root = histories[0][0]
    other_sid, alias = UUID(await add_session(h,h.user)), uuid4()
    async with h.factory() as db:
        foreign_task = Task(session_id=other_sid,status=TaskStatus.SUCCESS,idempotency_key='foreign',failure_reason='foreign-secret')
        db.add(foreign_task); await db.flush()
        first = await db.get(Run,root)
        first.task_id = foreign_task.task_id
        db.add(Run(run_id=alias,public_run_id=root,session_id=other_sid,idempotency_key='malformed'))
        await db.commit()
    for admin in (False,True):
        hdr=headers('admin' if admin else h.user['user_id'])
        prefix='/api/v1' + ('/admin' if admin else '')
        for suffix in ('diagnostics','invocations'):
            bad = await h.client.get(f'{prefix}/sessions/{other_sid}/runs/{alias}/{suffix}',headers=hdr)
            assert bad.status_code == 404
        response = await h.client.get(path(h,root,'diagnostics',admin=admin),headers=hdr)
        assert response.status_code == 200 and response.json()['task'] is None
        assert 'foreign-secret' not in response.text
        body = (await h.client.get(path(h,root,'invocations',admin=admin),headers=hdr)).json()
        assert {item['invocation_id'] for item in body['items']} == {str(root)}


@pytest.mark.asyncio
async def test_diagnostic_queries_work_in_read_only_transactions(resources):
    h = resources
    await seed(h,'waiting_executor')
    async with h.factory() as db:
        rid = await db.scalar(select(Task.root_run_id).where(Task.session_id == UUID(h.session_id)))
    # HTTP auth deliberately retains its existing User FOR SHARE barrier.
    # Prove the diagnostic query layer itself performs no writes or row locks.
    async with h.factory() as db:
        await db.execute(text('SET TRANSACTION READ ONLY'))
        for actor in (h.internal_user, None):
            diagnostic = await run_diagnostics.read_diagnostics(db,UUID(h.session_id),rid,user_id=actor)
            assert diagnostic.run_id == rid and diagnostic.task.status == TaskStatus.WAITING_INPUT
            history = await run_diagnostics.list_invocations(db,UUID(h.session_id),rid,ListParams(),user_id=actor)
            assert len(history['items']) == 1 and history['items'][0].run_id == rid


@pytest.mark.asyncio
async def test_owner_visibility_is_rechecked_between_identity_and_page(resources,monkeypatch):
    h = resources
    _, histories = await many_tasks(h,count=1,invocation_count=1)
    root = histories[0][0]
    original = run_diagnostics.fetch_page
    async def hide_then_fetch(*args,**kwargs):
        async with h.factory() as db:
            (await db.get(Session,UUID(h.session_id))).delete_yn = DeleteYN.Y
            await db.commit()
        return await original(*args,**kwargs)
    monkeypatch.setattr(run_diagnostics,'fetch_page',hide_then_fetch)
    response = await h.client.get(path(h,root,'invocations'),headers=headers(h.user['user_id']))
    assert response.status_code == 200, response.text
    assert response.json() == {'items':[], 'page':{'has_next':False,'next_cursor':None}}


@pytest.mark.asyncio
async def test_real_admission_and_worker_resumes_keep_run_task_and_history(runtime,monkeypatch):
    from unittest.mock import AsyncMock
    import api_service.runs.execution as execution
    from tests.api_service.test_run_cleanup_postgres import enqueue
    from tests.api_service.test_public_run_postgres import execute, waiting, state, resume
    h = runtime
    monkeypatch.setattr(execution,'ainvoke_user_turn',AsyncMock(return_value=waiting()))
    monkeypatch.setattr(execution,'ainvoke_resume',AsyncMock(return_value=waiting()))
    first = await enqueue(h)
    task_id = first['task_id']
    for index in range(3):
        await execute()
        current = await state(h,first['run_id'])
        diag = await h.client.get(path(h,first['run_id'],'diagnostics'),headers=headers(h.user['user_id']))
        assert diag.status_code == 200, diag.text
        assert diag.json()['run_id'] == first['run_id'] and diag.json()['task']['task_id'] == task_id
        response = await h.client.get(path(h,first['run_id'],'invocations'),headers=headers(h.user['user_id']))
        assert response.status_code == 200, response.text
        items = response.json()['items']
        assert len(items) == index+1 and len({item['invocation_id'] for item in items}) == index+1
        assert {item['run_id'] for item in items} == {first['run_id']}
        assert {item['task_id'] for item in items} == {task_id}
        if index < 2:
            response = await resume(h,current,key=f'diagnostic-resume-{index}')
            assert response.status_code == 202, response.text
