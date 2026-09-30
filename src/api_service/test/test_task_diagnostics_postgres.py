"""Task read-only diagnostics on the guarded, disposable PostgreSQL DB."""
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update

from api_service.core.enums import AgentRunStatus, DeleteYN, TaskStatus
from api_service.models.common.agent_run_model import AgentRunModel as Run
from api_service.models.common.project_model import ProjectModel as Project
from api_service.models.common.session_model import SessionModel as Session
from api_service.models.common.session_execution_model import SessionExecutionModel as Owner
from api_service.models.common.task_model import TaskModel as Task
from api_service.models.common.user_model import UserModel as User
from api_service.test.test_user_identity_postgres import database_url, harness, headers, add_session, add_user
from api_service.test.test_run_cleanup_postgres import runtime
from api_service.test.test_crud_guards_postgres import resources, seed
from api_service.test.test_read_queries_postgres import trace_reads


async def historical(h):
    await seed(h, 'finished_history')
    async with h.factory() as db:
        return await db.scalar(select(Task.task_id).where(Task.session_id == UUID(h.session_id)))


async def read(h, task_id, *, admin=False, actor=None):
    path = '/api/v1' + ('/admin' if admin else '') + f'/tasks/{task_id}'
    return await h.client.get(path, headers=headers(actor or ('admin' if admin else h.user['user_id'])))


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['pending','running','waiting_input','waiting_executor','recovery','finished_history'])
async def test_task_state_is_not_session_availability(resources, case):
    h = resources
    await seed(h, case)
    async with h.factory() as db:
        task = await db.scalar(select(Task).where(Task.session_id == UUID(h.session_id)))
    with trace_reads(h) as queries:
        response = await read(h, task.task_id)
    assert response.status_code == 200, response.text
    body = response.json()
    busy = case != 'finished_history'
    assert body['is_unfinished'] == busy
    assert 'is_active' not in body
    assert body['public_run_id'] == str(task.root_run_id)
    assert body['status'] == task.status.value
    assert body['session_work']['can_start_new_run'] == (not busy)
    assert body['session_work']['has_unfinished_work'] == busy
    assert body['session_work']['execution']['ownership_held'] is False
    assert body['session_work']['blocking_reasons'] == (['unfinished_task'] if busy else [])
    assert len(queries) == 2  # Authentication + one diagnostic snapshot, no N+1.
    assert all('lock_token' not in q['columns'] and 'token' not in q['columns'] for q in queries)
    assert body['observed_at']


@pytest.mark.asyncio
@pytest.mark.parametrize('case,reason', [
    ('pending','unfinished_task'), ('recovery','unfinished_task'),
    ('owner_live','execution_held_or_uncertain'),('owner_recovery','execution_held_or_uncertain'),
    ('orphan_pending','unfinished_run'),('orphan_running','unfinished_run'),('orphan_interrupted','unfinished_run'),
    ('llm_queued','unfinished_llm'),('llm_running','unfinished_llm'),
])
async def test_finished_task_reports_other_session_blockers(resources, case, reason):
    h = resources
    tid = await historical(h)
    await seed(h, case)
    response = await read(h, tid)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['is_unfinished'] is False
    assert body['session_work']['has_unfinished_work'] is True
    assert body['session_work']['can_start_new_run'] is False
    assert body['session_work']['blocking_reasons'] == [reason]


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['api_run', 'executor_event'])
async def test_old_heartbeat_is_not_expiry_and_no_owner_tokens_leak(resources, kind):
    h = resources
    tid = await historical(h)
    stamp = datetime.now(timezone.utc) - timedelta(days=8)
    token, owner_id = uuid4(), uuid4()
    async with h.factory() as db:
        db.add(Owner(session_id=UUID(h.session_id), token=token, owner_id=owner_id,
            owner_kind=kind, owner_process='test-pod:worker', acquired_at=stamp, heartbeat_at=stamp))
        await db.commit()
    for admin in (False, True):
        response = await read(h, tid, admin=admin)
        assert response.status_code == 200, response.text
        work = response.json()['session_work']
        assert work['execution']['ownership_held'] is True
        assert work['execution']['owner_id'] == str(owner_id)
        assert work['execution']['owner_kind'] == kind
        assert work['execution']['heartbeat_at']
        assert work['can_start_new_run'] is False
        assert str(token) not in response.text and 'lock_token' not in response.text
    async with h.factory() as db:
        owner = await db.get(Owner, UUID(h.session_id))
        assert owner.token == token and owner.heartbeat_at == stamp and not owner.recovery_required
        # Released rows retain old owner metadata internally. Do not call it the current owner.
        owner.token = None
        await db.commit()
    work = (await read(h, tid)).json()['session_work']
    assert work['can_start_new_run'] is True
    assert work['execution']['ownership_held'] is False
    assert work['execution']['owner_process'] is None
    assert work['execution']['owner_id'] is None
    async with h.factory() as db:
        await db.execute(update(Owner).where(Owner.session_id == UUID(h.session_id)).values(
            recovery_required=True, recovery_reason='termination_uncertain'))
        await db.commit()
    work = (await read(h, tid)).json()['session_work']
    assert work['can_start_new_run'] is False
    assert work['execution']['ownership_held'] is False
    assert work['execution']['recovery_required'] is True
    assert work['execution']['recovery_reason'] == 'termination_uncertain'


@pytest.mark.asyncio
async def test_other_sessions_do_not_block_this_session(resources):
    h = resources
    tid = await historical(h)
    other_sid = UUID(await add_session(h, h.user))
    async with h.factory() as db:
        db.add(Task(session_id=other_sid, status=TaskStatus.RUNNING, idempotency_key='other'))
        db.add(Owner(session_id=other_sid, token=uuid4(), recovery_required=True))
        await db.commit()
    body = (await read(h, tid)).json()
    assert body['session_work']['can_start_new_run'] is True
    assert body['session_work']['has_unfinished_work'] is False


@pytest.mark.asyncio
async def test_admin_routes_do_not_expand_owner_routes(resources):
    h = resources
    tid = await historical(h)
    await add_user(h, 'another')
    paths = [f'/tasks/{tid}', f'/tasks/{tid}/runs', f'/sessions/{h.session_id}/tasks']
    for suffix in paths:
        for actor in ('admin','another'):
            assert (await h.client.get('/api/v1'+suffix, headers=headers(actor))).status_code == 404
        assert (await h.client.get('/api/v1/admin'+suffix, headers=headers(h.user['user_id']))).status_code == 403
        assert (await h.client.get('/api/v1/admin'+suffix)).status_code == 401
        assert (await h.client.get('/api/v1/admin'+suffix, headers=headers('missing'))).status_code == 401
        assert (await h.client.get('/api/v1/admin'+suffix, headers=headers('admin'))).status_code == 200
    for suffix in (f'/tasks/{uuid4()}', f'/tasks/{uuid4()}/runs', f'/sessions/{uuid4()}/tasks'):
        assert (await h.client.get('/api/v1/admin'+suffix, headers=headers('admin'))).status_code == 404
    empty_sid = await add_session(h, h.user)
    result = await h.client.get(f'/api/v1/sessions/{empty_sid}/tasks', headers=headers(h.user['user_id']))
    assert result.json() == {'items':[], 'page':{'has_next':False, 'next_cursor':None}}


@pytest.mark.asyncio
@pytest.mark.parametrize('model', [Session, Project, User])
async def test_admin_can_diagnose_hidden_resources_read_only(resources, model):
    h = resources
    tid = await historical(h)
    await seed(h, 'owner_recovery')
    target = {Session:UUID(h.session_id), Project:UUID(h.project_id), User:h.internal_user}[model]
    async with h.factory() as db:
        entity = await db.get(model, target)
        entity.delete_yn = DeleteYN.Y
        await db.commit()
    expected = 401 if model is User else 404
    paths = [f'/tasks/{tid}',f'/tasks/{tid}/runs',f'/sessions/{h.session_id}/tasks']
    for path in paths:
        assert (await h.client.get('/api/v1'+path, headers=headers(h.user['user_id']))).status_code == expected
        result = await h.client.get('/api/v1/admin'+path, headers=headers())
        assert result.status_code == 200, result.text
    work = (await read(h, tid, admin=True)).json()['session_work']
    assert work['resources_active'] is False and work['can_start_new_run'] is False
    assert work['has_unfinished_work'] is True
    assert work['blocking_reasons'] == ['resources_inactive','execution_held_or_uncertain']
    async with h.factory() as db:
        assert (await db.get(model,target)).delete_yn == DeleteYN.Y
        assert (await db.get(Owner,UUID(h.session_id))).recovery_required is True


async def many_tasks(h, count=9, invocation_count=9):
    stamp = datetime(2026,1,1,tzinfo=timezone.utc)
    tids, invocation_ids = [], []
    async with h.factory() as db:
        for i in range(count):
            task = Task(session_id=UUID(h.session_id), status=TaskStatus.SUCCESS,
                        idempotency_key=f'task-{i}', created_at=stamp)
            db.add(task); await db.flush()
            tids.append(task.task_id)
            ids = [uuid4() for _ in range(invocation_count)]
            # Each Task's invocations deliberately share a timestamp.
            for rid in ids:
                db.add(Run(run_id=rid,public_run_id=ids[0],session_id=UUID(h.session_id),task_id=task.task_id,
                    status=AgentRunStatus.SUCCESS, idempotency_key=str(rid),created_at=stamp,
                    input_json={'do_not_read':'x'*8192},request_payload={'private':'y'*8192}))
            await db.flush()
            task.root_run_id = ids[0]
            invocation_ids.append(ids)
        await db.commit()
    return tids, invocation_ids


@pytest.mark.asyncio
@pytest.mark.parametrize('admin', [False, True])
@pytest.mark.parametrize('sort', ['created_at','-created_at'])
@pytest.mark.parametrize('limit', [1,4,200])
async def test_cursor_pagination_query_budget_and_distinct_ids(resources, admin, sort, limit):
    h = resources
    tids, invocation_ids = await many_tasks(h)
    prefix = '/api/v1' + ('/admin' if admin else '')
    hdr = headers('admin' if admin else h.user['user_id'])
    for path, ids, field in [(f'/sessions/{h.session_id}/tasks',tids,'task_id'),
                             (f'/tasks/{tids[0]}/runs',invocation_ids[0],'invocation_id')]:
        cursor, seen = None, []
        while True:
            params = {'sort':sort, 'limit':limit}
            if cursor:
                params['cursor'] = cursor
            with trace_reads(h) as queries:
                response = await h.client.get(prefix+path, params=params, headers=hdr)
            assert response.status_code == 200, response.text
            assert len(queries) == 3
            body = response.json()
            seen.extend(UUID(row[field]) for row in body['items'])
            if field == 'invocation_id':
                assert all(row['public_run_id'] == str(invocation_ids[0][0]) for row in body['items'])
                assert all('id' not in row for row in body['items'])
                for query in queries:
                    assert '.input' not in query['sql'] and '.request_payload' not in query['sql']
            if not body['page']['has_next']:
                assert body['page']['next_cursor'] is None
                break
            cursor = body['page']['next_cursor']
            assert cursor and len(seen) <= len(ids)
        assert seen == sorted(ids,reverse=sort.startswith('-'))
        for params, status, size in [({'cursor':'broken'},400,None),({'limit':201},422,None),
                ({'created_at_to':'2026-01-01T00:00:00Z'},200,0),
                ({'created_at_from':'2026-01-01T00:00:00Z'},200,9)]:
            result = await h.client.get(prefix+path, params=params, headers=hdr)
            assert result.status_code == status, result.text
            if size is not None:
                assert len(result.json()['items']) == size


@pytest.mark.asyncio
async def test_long_invocation_history_is_bounded(resources):
    h = resources
    tids, invocation_ids = await many_tasks(h, count=1, invocation_count=205)
    path = f'/api/v1/tasks/{tids[0]}/runs'
    response = await h.client.get(path,params={'limit':200},headers=headers(h.user['user_id']))
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body['items']) == 200 and body['page']['has_next']
    rest = await h.client.get(path,params={'limit':200,'cursor':body['page']['next_cursor']},headers=headers(h.user['user_id']))
    assert rest.status_code == 200 and len(rest.json()['items']) == 5
    assert rest.json()['page']['has_next'] is False
    assert len({row['invocation_id'] for row in body['items']+rest.json()['items']}) == 205


@pytest.mark.asyncio
async def test_removed_commands_openapi_and_message_deferral(resources):
    h = resources
    tid = await historical(h)
    paths = h.app.openapi()['paths']
    for suffix, method in [('resume','post'),('cancel','post'),('stream','get')]:
        path = '/api/v1/tasks/{task_id}/'+suffix
        assert path not in paths
        result = await h.client.request(method.upper(),f'/api/v1/tasks/{tid}/{suffix}',headers=headers(h.user['user_id']))
        assert result.status_code == 404
    assert '/api/v1/sessions/{session_id}/runs/{run_id}/resume' not in paths
    assert 'post' in paths['/api/v1/sessions/{session_id}/runs']
    assert 'post' in paths['/api/v1/sessions/{session_id}/runs/stream']
    assert 'post' in paths['/api/v1/sessions/{session_id}/runs/{run_id}/cancel']
    assert 'get' in paths['/api/v1/sessions/{session_id}/runs/{run_id}/stream']
    assert not paths['/api/v1/tasks/{task_id}']['get'].get('deprecated',False)
    assert 'post' in paths['/api/v1/messages']
    assert 'patch' in paths['/api/v1/messages/{message_id}']
    assert 'delete' in paths['/api/v1/messages/{message_id}']
