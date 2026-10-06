"""Project read/request policy and prompt pinning on a disposable PostgreSQL."""
from datetime import datetime, timezone
from uuid import UUID, uuid4
from unittest.mock import AsyncMock

import pytest

from dtest.contracts.enums import DeleteYN
from dtest.infrastructure.database.models.project_model import ProjectModel as Project
from dtest.infrastructure.database.models.session_model import SessionModel as Session
from dtest.infrastructure.database.models.message_model import MessageModel as Message
from dtest.application.runs.graph_invocation import GraphInvocation
from tests.api_service.test_user_identity_postgres import database_url, harness, initialize, add_user, headers
from tests.api_service.test_read_queries_postgres import trace_reads
from tests.api_service.test_run_cleanup_postgres import runtime, enqueue
from tests.api_service.test_initial_request_recovery_postgres import real_initial, saved
from tests.api_service.test_public_run_postgres import state, resume, execute

SUMMARY_FIELDS = {'id', 'name', 'is_default', 'created_at', 'updated_at'}
DETAIL_FIELDS = SUMMARY_FIELDS | {'system_prompt', 'prompt_version'}


async def sample(h, count=18):
    await initialize(h)
    user = await add_user(h)
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    async with h.factory() as db:
        default = await db.get(Project, UUID(user['default_project_id']))
        default.created_at = stamp
        default.system_prompt = 'private long instructions ' * 2000
        projects = [default]
        for index in range(count - 1):
            item = Project(user_id=default.user_id, project_name=f'project-{index}',
                created_at=stamp, system_prompt='private long instructions ' * 2000)
            db.add(item)
            projects.append(item)
        db.add(Project(user_id=default.user_id, project_name='hidden', delete_yn=DeleteYN.Y))
        await db.commit()
    return user, projects


@pytest.mark.asyncio
@pytest.mark.parametrize('sort', ['created_at', '-created_at'])
@pytest.mark.parametrize('limit', [1, 7, 200])
async def test_project_summary_pages_do_not_load_prompts_or_children(harness, sort, limit):
    h = harness
    user, projects = await sample(h)
    cursor, seen = None, []
    while True:
        params = {'limit': limit, 'sort': sort}
        if cursor:
            params['cursor'] = cursor
        with trace_reads(h) as reads:
            response = await h.client.get('/api/v1/projects', params=params, headers=headers(user['user_id']))
        assert response.status_code == 200, response.text
        assert response.headers['cache-control'] == 'no-store'
        body = response.json()
        assert len(reads) == 2 and len(reads[-1]['columns']) == 5
        assert reads[-1]['rows'] <= limit + 1
        assert all(term not in reads[-1]['sql'] for term in ('system_prompt', 'prompt_version', 'sessions', 'messages'))
        assert all(set(item) == SUMMARY_FIELDS for item in body['items'])
        seen.extend(UUID(item['id']) for item in body['items'])
        if not body['page']['has_next']:
            assert body['page']['next_cursor'] is None
            break
        cursor = body['page']['next_cursor']
        assert cursor and len(seen) <= len(projects)
    assert seen == sorted((item.project_id for item in projects), reverse=sort.startswith('-'))
    assert len(seen) == len(set(seen)) == len(projects)
    detail = await h.client.get(f"/api/v1/projects/{projects[0].project_id}", headers=headers(user['user_id']))
    assert detail.status_code == 200 and set(detail.json()) == DETAIL_FIELDS
    assert detail.headers['cache-control'] == 'no-store'
    assert detail.json()['system_prompt'] == projects[0].system_prompt


@pytest.mark.asyncio
async def test_project_default_page_large_history_and_date_boundaries(harness):
    h = harness
    user, projects = await sample(h, count=205)
    hdr = headers(user['user_id'])
    first = await h.client.get('/api/v1/projects', headers=hdr)
    assert first.status_code == 200 and len(first.json()['items']) == 50
    params = {'limit': 200}
    first = await h.client.get('/api/v1/projects', headers=hdr, params=params)
    assert len(first.json()['items']) == 200
    params['cursor'] = first.json()['page']['next_cursor']
    last = await h.client.get('/api/v1/projects', headers=hdr, params=params)
    assert len(last.json()['items']) == 5 and last.json()['page'] == {'has_next': False, 'next_cursor': None}
    assert len({item['id'] for item in first.json()['items'] + last.json()['items']}) == 205
    for params, expected in [({'created_at_to': '2026-01-01T00:00:00Z'}, 0),
                             ({'created_at_from': '2026-01-01T00:00:00Z', 'limit': 200}, 200),
                             ({'created_at_from': '2026-01-01T00:00:01Z'}, 0)]:
        response = await h.client.get('/api/v1/projects', headers=hdr, params=params)
        assert response.status_code == 200 and len(response.json()['items']) == expected
    for params, code in [({'cursor': 'broken'}, 400), ({'limit': 201}, 422), ({'sort': 'name'}, 422),
                         ({'created_at_from': '2026-02-01', 'created_at_to': '2026-01-01'}, 422)]:
        response = await h.client.get('/api/v1/projects', headers=hdr, params=params)
        assert response.status_code == code, response.text
    missing = await h.client.get('/api/v1/projects')
    assert missing.status_code == 401
    admin = await h.client.get('/api/v1/projects', headers=headers())
    assert admin.status_code == 200
    assert not {str(item.project_id) for item in projects} & {item['id'] for item in admin.json()['items']}


@pytest.mark.asyncio
async def test_project_requests_are_atomic_and_explicit_null_is_rejected(harness):
    h = harness
    await initialize(h)
    user = await add_user(h)
    hdr = headers(user['user_id'])
    for payload in [{'project_name': 'invalid', 'user_id': str(uuid4())},
                    {'project_name': 'invalid', 'is_default': True},
                    {'project_name': 'invalid', 'prompt_version': 99},
                    {'name': 'invalid'}, {'project_name': None},
                    {'project_name': 'invalid', 'system_prompt': None}]:
        result = await h.client.post('/api/v1/projects', headers=hdr, json=payload)
        assert result.status_code == 422, result.text
    for name in ('', '   '):
        result = await h.client.post('/api/v1/projects', headers=hdr, json={'project_name': name})
        assert result.status_code == 409, result.text
    result = await h.client.post('/api/v1/projects', headers=hdr,
        json={'project_name': '  My    Project  ', 'system_prompt': '  exact prompt\n'})
    assert result.status_code == 201, result.text
    assert set(result.json()) == DETAIL_FIELDS
    assert result.headers['location'] == f"/api/v1/projects/{result.json()['id']}"
    assert result.json()['name'] == 'My Project' and result.json()['system_prompt'] == '  exact prompt\n'
    endpoint = result.headers['location']
    baseline = result.json()
    for payload in [{}, {'project_name': None}, {'system_prompt': None}, {'project_name': ''},
                    {'project_name': '   ', 'system_prompt': 'must not apply'},
                    {'project_name': 'must not apply', 'system_prompt': None},
                    {'project_name': None, 'system_prompt': 'must not apply'},
                    {'project_name': 'must not apply', 'project_id': str(uuid4())},
                    {'system_prompt': 'must not apply', 'project_memory': 'not a project field'}]:
        patch = await h.client.patch(endpoint, headers=hdr, json=payload)
        assert patch.status_code == 422, patch.text
        assert (await h.client.get(endpoint, headers=hdr)).json() == baseline
    duplicate = await h.client.post('/api/v1/projects', headers=hdr, json={'project_name': 'My Project'})
    assert duplicate.status_code == 409
    default = f"/api/v1/projects/{user['default_project_id']}"
    before = (await h.client.get(default, headers=hdr)).json()
    rename = await h.client.patch(default, headers=hdr, json={'project_name': 'new', 'system_prompt': 'must not apply'})
    assert rename.status_code == 409 and (await h.client.get(default, headers=hdr)).json() == before
    assert (await h.client.delete(default, headers=hdr)).status_code == 409


@pytest.mark.asyncio
async def test_prompt_versions_change_only_for_changed_content_and_empty_string_clears(harness):
    h = harness
    await initialize(h)
    user = await add_user(h)
    hdr = headers(user['user_id'])
    create = await h.client.post('/api/v1/projects', headers=hdr, json={'project_name': 'analysis'})
    assert create.status_code == 201 and create.json()['system_prompt'] == '' and create.json()['prompt_version'] == 1
    endpoint = create.headers['location']
    for payload, expected, version in [({'project_name': 'renamed'}, '', 1),
        ({'system_prompt': 'v2'}, 'v2', 2), ({'system_prompt': 'v2'}, 'v2', 2),
        ({'system_prompt': ''}, '', 3), ({'system_prompt': ''}, '', 3),
        ({'project_name': 'final', 'system_prompt': 'v4'}, 'v4', 4)]:
        response = await h.client.patch(endpoint, headers=hdr, json=payload)
        assert response.status_code == 200, response.text
        assert response.json()['system_prompt'] == expected and response.json()['prompt_version'] == version
    assert (await h.client.get(endpoint, headers=headers())).status_code == 404
    assert (await h.client.patch(endpoint, headers=headers(), json={'system_prompt': 'unauthorized'})).status_code == 404
    assert (await h.client.delete(endpoint, headers=headers())).status_code == 404


@pytest.mark.asyncio
async def test_deleted_project_is_hidden_and_cascade_delete_has_no_response_body(harness):
    h = harness
    await initialize(h)
    user = await add_user(h)
    hdr = headers(user['user_id'])
    created = await h.client.post('/api/v1/projects', headers=hdr, json={'project_name': 'delete'})
    pid = UUID(created.json()['id'])
    session = await h.client.post(f'/api/v1/projects/{pid}/sessions', headers=hdr, json={'session_name': 'child'})
    sid = UUID(session.json()['id'])
    async with h.factory() as db:
        message = Message(session_id=sid, message_type='user', content_text='retain history')
        db.add(message)
        await db.commit()
        mid = message.message_id
    deleted = await h.client.delete(f'/api/v1/projects/{pid}', headers=hdr)
    assert deleted.status_code == 204 and deleted.content == b''
    assert (await h.client.get(f'/api/v1/projects/{pid}', headers=hdr)).status_code == 404
    assert (await h.client.patch(f'/api/v1/projects/{pid}', headers=hdr, json={'system_prompt': 'revive'})).status_code == 404
    assert (await h.client.delete(f'/api/v1/projects/{pid}', headers=hdr)).status_code == 404
    items = (await h.client.get('/api/v1/projects', headers=hdr)).json()['items']
    assert str(pid) not in {item['id'] for item in items}
    async with h.factory() as db:
        assert (await db.get(Project, pid)).delete_yn == DeleteYN.Y
        assert (await db.get(Session, sid)).delete_yn == DeleteYN.Y
        assert (await db.get(Message, mid)).delete_yn == DeleteYN.Y
        assert (await db.get(Message, mid)).content_text == 'retain history'


@pytest.mark.asyncio
async def test_worker_start_pins_prompt_resume_keeps_it_and_new_run_loads_latest(real_initial, monkeypatch):
    h = real_initial
    hdr = headers(h.user['user_id'])
    endpoint = f"/api/v1/projects/{h.user['default_project_id']}"
    assert (await h.client.patch(endpoint, headers=hdr, json={'system_prompt': 'queued prompt'})).json()['prompt_version'] == 2
    first = await enqueue(h)
    assert (await h.client.patch(endpoint, headers=hdr, json={'system_prompt': 'start prompt'})).json()['prompt_version'] == 3
    await execute()
    snapshot = await saved(h, first['run_id'])
    assert snapshot.values['project_system_prompt'] == 'start prompt' and snapshot.values['project_prompt_version'] == 3
    assert (await h.client.patch(endpoint, headers=hdr, json={'system_prompt': 'future prompt'})).json()['prompt_version'] == 4
    current = await state(h, first['run_id'])
    assert current['status'] == 'waiting_input'
    with monkeypatch.context() as scope:
        scope.setattr(GraphInvocation, 'project_snapshot', AsyncMock(side_effect=AssertionError('resume reloaded current prompt')))
        assert (await resume(h, current)).status_code == 202
        await execute()
    assert (await state(h, first['run_id']))['status'] == 'success'
    snapshot = await saved(h, first['run_id'])
    assert snapshot.values['project_system_prompt'] == 'start prompt' and snapshot.values['project_prompt_version'] == 3
    # The new run shares the same session thread but must not inherit the old prompt.
    assert (await h.client.patch(endpoint, headers=hdr, json={'system_prompt': ''})).json()['prompt_version'] == 5
    h.calls['terminal'] = True
    second = await enqueue(h)
    assert second['run_id'] != first['run_id']
    await execute()
    assert (await state(h, second['run_id']))['status'] == 'success'
    snapshot = await saved(h, second['run_id'])
    assert snapshot.values['project_system_prompt'] == '' and snapshot.values['project_prompt_version'] == 5
    assert h.calls['entry'] == h.calls['model'] == 2
