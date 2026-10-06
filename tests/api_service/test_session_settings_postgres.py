from dtest.contracts.errors import ApplicationError
"""Real HTTP/PG session defaults and immutable settings, using a disposable DB."""
from dataclasses import replace
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from fastapi import HTTPException

import dtest.settings.loader as service_settings
from dtest.infrastructure.database.models.session_model import SessionModel
from dtest.application.resources.sessions import SessionService
from dtest.application.runs.project_context import load_project_snapshot
from tests.api_service.test_user_identity_postgres import database_url, harness, headers, initialize, add_user


@pytest_asyncio.fixture
async def sessions(harness, monkeypatch):
    h = harness
    old = service_settings.get_settings()
    monkeypatch.setattr(service_settings, '_snapshot', replace(old, agent=replace(old.agent,
        executor_runtime_profile='default', executor_runtime_profiles=('default', '3102311'))))
    await initialize(h)
    h.user = await add_user(h)
    h.path = f"/api/v1/projects/{h.user['default_project_id']}/sessions"
    return h


@pytest.mark.asyncio
@pytest.mark.parametrize('body', [{}, {'settings': {}}, {'settings': {'kernel_profile': None}},
    {'settings': {'kernel_profile': '3102311'}}])
async def test_create_persists_resolved_kernel_and_all_read_views_match(sessions, body):
    h = sessions
    created = await h.client.post(h.path, headers=headers(h.user['user_id']), json=body)
    assert created.status_code == 201, created.text
    resource = created.json()
    expected = (body.get('settings') or {}).get('kernel_profile') or 'default'
    assert resource['settings'] == {'kernel_profile': expected}
    current = await h.client.get(f"/api/v1/sessions/{resource['id']}", headers=headers(h.user['user_id']))
    page = await h.client.get(h.path, headers=headers(h.user['user_id']))
    assert current.json()['settings'] == resource['settings'] == page.json()['items'][0]['settings']
    async with h.factory() as db:
        row = await db.get(SessionModel, UUID(resource['id']))
        assert row.settings == {'kernel_profile': expected}
        snapshot = await load_project_snapshot(db, user_id=row.user_id, session_id=row.session_id)
        assert snapshot['kernel_profile'] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('body', [
    {'settings': {'kernel_profile': 'not-registered'}}, {'settings': {'kernel_profile': ''}},
    {'settings': {'kernel_profile': 'a/b'}}, {'settings': {'kernel_profile': 3102311}},
    {'settings': {'main_model_name': 'other'}}, {'settings': {'repair_level': 4}},
    {'settings': {'unexpected': 'value'}}, {'settings': None}, {'kernel_profile': 'default'},
    {'target_project_id': '11111111-1111-4111-8111-111111111111'},
])
async def test_invalid_create_does_not_persist_session(sessions, body):
    h = sessions
    rejected = await h.client.post(h.path, headers=headers(h.user['user_id']), json=body)
    assert rejected.status_code == 422, rejected.text
    async with h.factory() as db:
        assert await db.scalar(select(func.count()).select_from(SessionModel)) == 0


@pytest.mark.asyncio
async def test_default_and_allowlist_change_do_not_reassign_existing_session(sessions, monkeypatch):
    h = sessions
    created = (await h.client.post(h.path, headers=headers(h.user['user_id']), json={})).json()
    settings = service_settings.get_settings()
    monkeypatch.setattr(service_settings, '_snapshot', replace(settings, agent=replace(settings.agent,
        executor_runtime_profile='3102311', executor_runtime_profiles=('3102311',))))
    async with h.factory() as db:
        row = await db.get(SessionModel, UUID(created['id']))
        snapshot = await load_project_snapshot(db, user_id=row.user_id, session_id=row.session_id)
        assert snapshot['kernel_profile'] == 'default'
    new = await h.client.post(h.path, headers=headers(h.user['user_id']), json={})
    assert new.status_code == 201 and new.json()['settings'] == {'kernel_profile': '3102311'}
    rejected = await h.client.patch(f"/api/v1/sessions/{created['id']}", headers=headers(h.user['user_id']),
        json={'settings': {'kernel_profile': '3102311'}, 'session_name': 'must not change'})
    assert rejected.status_code == 422
    unchanged = await h.client.get(f"/api/v1/sessions/{created['id']}", headers=headers(h.user['user_id']))
    assert unchanged.json()['settings'] == {'kernel_profile': 'default'} and unchanged.json()['name'] == '새 대화'


@pytest.mark.asyncio
async def test_message_coordinator_uses_same_resolved_session_default(sessions):
    h = sessions
    response = await h.client.post('/api/v1/messages', headers=headers(h.user['user_id']),
        json={'content_text': 'internal session creation'})
    assert response.status_code == 201, response.text
    session = await h.client.get('/api/v1/sessions/'+response.json()['session_id'], headers=headers(h.user['user_id']))
    assert session.json()['settings'] == {'kernel_profile': 'default'}


@pytest.mark.asyncio
async def test_internal_creator_cannot_bypass_validation_and_legacy_json_is_preserved(sessions):
    h = sessions
    async with h.factory() as db:
        from dtest.infrastructure.database.models.project_model import ProjectModel
        project = await db.get(ProjectModel, UUID(h.user['default_project_id']))
        with pytest.raises((HTTPException, ApplicationError)) as failed:
            await SessionService.create_internal(db, user_id=project.user_id,
                project_id=project.project_id, settings={'unknown': True})
        assert failed.value.status_code == 422
        assert await db.scalar(select(func.count()).select_from(SessionModel)) == 0
        # Old unused metadata is not erased by a read or an unrelated rename.
        legacy = SessionModel(user_id=project.user_id, project_id=project.project_id,
            session_name='legacy', settings={'legacy_note': 'preserve'})
        db.add(legacy); await db.commit()
        legacy_id = str(legacy.session_id)
    renamed = await h.client.patch('/api/v1/sessions/'+legacy_id, headers=headers(h.user['user_id']),
        json={'session_name':'renamed legacy'})
    assert renamed.status_code == 200 and renamed.json()['settings'] == {'legacy_note':'preserve'}
