"""Read budgets and HTTP contracts on disposable, migrated PostgreSQL.

DTEST_QUERY_REPORT_DIR optionally records SELECT counts/returned rows/columns.
These are query-work measurements, not user latency or physical DB pages read.
"""
import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, insert, select, update

from api_service.core.enums import AgentRunStatus, DeleteYN, MessageType
from api_service.models.common.agent_run_model import AgentRunModel as Run
from api_service.models.common.message_model import MessageModel as Message
from api_service.models.common.project_model import ProjectModel as Project
from api_service.models.common.session_model import SessionModel as Session
from api_service.test.test_user_identity_postgres import database_url, harness, initialize, add_user, add_session, headers


@contextmanager
def trace_reads(h):
    reads = []
    def finished(conn, cursor, statement, parameters, context, executemany):
        # Fixture substitutes a DB lookup for Redis; do not count that artificial
        # query. The production active-user/role/admission query is still counted.
        if context.execution_options.get('test_identity_lookup'):
            return
        if statement.lstrip().upper().startswith('SELECT'):
            reads.append({'sql': statement, 'rows': cursor.rowcount,
                          'columns': [column[0] for column in cursor.description]})
    event.listen(h.engine.sync_engine, 'after_cursor_execute', finished)
    try:
        yield reads
    finally:
        event.remove(h.engine.sync_engine, 'after_cursor_execute', finished)


async def sample(h, message_count):
    await initialize(h)
    user = await add_user(h)
    sid = UUID(await add_session(h, user))
    pid = UUID(user['default_project_id'])
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    roots, latest_ids = [], []
    async with h.factory() as db:
        uid = await db.scalar(select(Project.user_id).where(Project.project_id == pid))
        db.add_all([Session(user_id=uid, project_id=pid, session_name=f'sibling-{i}') for i in range(19)])
        await db.execute(insert(Message), [{'session_id':sid, 'message_type':MessageType.USER,
            'content':[{'type':'text','text':'x'*1024}], 'content_text':'x'*1024} for _ in range(message_count)])
        # All roots share a timestamp, so pagination must use UUID tie-breaking.
        for i in range(25):
            root_id, second_id, latest_id = uuid4(), uuid4(), uuid4()
            roots.append(root_id); latest_ids.append(latest_id)
            for ordinal, rid in enumerate((root_id, second_id, latest_id)):
                db.add(Run(run_id=rid, public_run_id=root_id, session_id=sid,
                    idempotency_key=str(rid), created_at=stamp+timedelta(seconds=ordinal),
                    updated_at=stamp+timedelta(seconds=ordinal),
                    status=AgentRunStatus.INTERRUPTED, interrupt=[{'kind':'USER_APPROVAL','step':ordinal}],
                    input_json={'private_input':'x'*8192}, command_json={'private_command':'y'*8192},
                    request_payload={'private_request':'z'*8192}, metadata_json={'checkpoint_run_id':str(root_id),'private_metadata':'m'*8192},
                    redis_result={'private_result':'r'*8192}, agent_response={'result':'visible if terminal'}))
        await db.commit()
    return user, pid, sid, roots, latest_ids


@pytest.mark.asyncio
@pytest.mark.parametrize('message_count', [10, 1000])
async def test_http_read_measurements(harness, message_count):
    h = harness
    user, pid, sid, roots, latest_ids = await sample(h, message_count)
    base = f'/api/v1/sessions/{sid}/runs'
    paths = {'project':f'/api/v1/projects/{pid}', 'session':f'/api/v1/sessions/{sid}',
             'run':f'{base}/{roots[0]}', 'run_alias':f'{base}/{latest_ids[0]}',
             'runs':base+'?limit=10', 'runs_empty':base+'?created_at_from=2027-01-01T00:00:00Z'}
    report = {}
    responses = {}
    for name, path in paths.items():
        with trace_reads(h) as reads:
            response = await h.client.get(path, headers=headers(user['user_id']))
        assert response.status_code == 200, response.text
        responses[name] = response.json()
        assert all(r['rows'] >= 0 for r in reads)
        report[name] = {'selects':len(reads), 'returned_rows':sum(r['rows'] for r in reads),
                        'queries':reads}
    assert set(responses['project']) == {'id','name','system_prompt','prompt_version','is_default','created_at','updated_at'}
    assert set(responses['session']) == {'id','project_id','name','current_leaf_message_id','settings','created_at','updated_at','active_run','availability'}
    assert responses['run'] == responses['run_alias']
    assert responses['run']['run_id'] == str(roots[0])
    assert responses['run']['resume_token'] == str(latest_ids[0])
    assert responses['run']['status'] == 'waiting_input'
    assert responses['run']['result'] is None
    assert len(responses['runs']['items']) == 10 and responses['runs']['page']['has_next']
    assert responses['runs_empty'] == {'items':[], 'page':{'has_next':False,'next_cursor':None}}
    destination = os.getenv('DTEST_QUERY_REPORT_DIR')
    if destination:
        Path(destination).mkdir(parents=True, exist_ok=True)
        (Path(destination)/f'messages-{message_count}.json').write_text(json.dumps(report, indent=2))

    # HTTP read budgets include authentication and session ownership queries.
    assert report['project']['selects'] == 2 and report['project']['returned_rows'] == 2
    assert report['session']['selects'] == 2 and report['session']['returned_rows'] == 2
    assert not any('messages' in q['sql'] for q in report['project']['queries'])
    assert not any('FROM messages' in q['sql'] for q in report['session']['queries'])
    for name in ('run', 'run_alias'):
        assert report[name]['selects'] == 3
        assert report[name]['returned_rows'] == 3
    assert report['runs']['selects'] == 4
    assert report['runs_empty']['selects'] == 3
    for name in ('run', 'run_alias', 'runs'):
        queries = report[name]['queries']
        for q in queries:
            if 'FROM agent_runs' in q['sql']:
                assert len(q['columns']) <= 25
                assert not any(private in q['sql'] for private in
                    ('.input', '.command', '.request_payload', '.redis_result', '.idempotency_key'))


@pytest.mark.asyncio
@pytest.mark.parametrize('sort', ['created_at', '-created_at'])
@pytest.mark.parametrize('limit', [1, 7, 200])
async def test_run_page_boundaries_and_fixed_query_count(harness, sort, limit):
    h = harness
    user, pid, sid, roots, _ = await sample(h, 10)
    base = f'/api/v1/sessions/{sid}/runs'
    cursor, seen = None, []
    while True:
        params = {'sort':sort, 'limit':limit}
        if cursor:
            params['cursor'] = cursor
        with trace_reads(h) as reads:
            response = await h.client.get(base, params=params, headers=headers(user['user_id']))
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(reads) == 4  # No query per Run, even with many HITL invocations.
        seen.extend(UUID(item['run_id']) for item in body['items'])
        assert all(item['status'] == 'waiting_input' for item in body['items'])
        if not body['page']['has_next']:
            assert body['page']['next_cursor'] is None
            break
        cursor = body['page']['next_cursor']
        assert cursor
        assert len(seen) <= 25
    assert seen == sorted(roots, reverse=sort.startswith('-'))
    # Same created_at for all roots: [from, to), never latest invocation time.
    for params, count in [({'created_at_from':'2026-01-01T00:00:00Z'},25),
                          ({'created_at_to':'2026-01-01T00:00:00Z'},0),
                          ({'created_at_from':'2026-01-01T00:00:01Z'},0)]:
        result = await h.client.get(base, params={'limit':200,**params}, headers=headers(user['user_id']))
        assert result.status_code == 200 and len(result.json()['items']) == count
    invalid = await h.client.get(base, params={'cursor':'invalid'}, headers=headers(user['user_id']))
    assert invalid.status_code == 400


@pytest.mark.asyncio
async def test_read_ownership_missing_and_soft_deleted(harness):
    h = harness
    user, pid, sid, roots, _ = await sample(h, 10)
    paths = [f'/api/v1/projects/{pid}', f'/api/v1/sessions/{sid}',
             f'/api/v1/sessions/{sid}/runs', f'/api/v1/sessions/{sid}/runs/{roots[0]}']
    for path in paths:
        assert (await h.client.get(path, headers=headers('admin'))).status_code == 404
        assert (await h.client.get(path)).status_code == 401
    for path in [f'/api/v1/projects/{uuid4()}',f'/api/v1/sessions/{uuid4()}',
                 f'/api/v1/sessions/{sid}/runs/{uuid4()}']:
        assert (await h.client.get(path, headers=headers(user['user_id']))).status_code == 404
    async with h.factory() as db:
        await db.execute(update(Session).where(Session.session_id == sid).values(delete_yn=DeleteYN.Y))
        await db.execute(update(Project).where(Project.project_id == pid).values(delete_yn=DeleteYN.Y))
        await db.commit()
    for path in paths:
        assert (await h.client.get(path, headers=headers(user['user_id']))).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize('checkpoint', [None, '', False, 'explicit'])
async def test_checkpoint_projection_and_no_orm_side_effects(harness, checkpoint):
    from api_service.services.public_run_service import PublicRunService
    h = harness
    user, pid, sid, roots, latest_ids = await sample(h, 10)
    explicit = uuid4()
    async with h.factory() as db:
        await db.execute(update(Run).where(Run.run_id == roots[0]).values(
            metadata_json={'checkpoint_run_id':str(explicit) if checkpoint == 'explicit' else checkpoint}))
        await db.commit()
        uid = await db.scalar(select(Project.user_id).where(Project.project_id == pid))
        root = await db.get(Run, roots[0])
        original_input = root.input_json
        # Keep an unflushed write in the identity map. Read-only scalar snapshots
        # must not refresh it away or populate partially loaded mutable entities.
        root.input_json = {'unflushed':'preserve'}
        async with h.factory() as writer:
            await writer.execute(update(Run).where(Run.run_id == latest_ids[0]).values(
                status=AgentRunStatus.SUCCESS, agent_response={'answer':'final'},
                completed_at=datetime(2026,1,2,tzinfo=timezone.utc)))
            await writer.commit()
        result = await PublicRunService.read(db, uid, sid, roots[0])
        assert result.status == 'success' and result.result == {'answer':'final'}
        assert result.interrupt is None and result.resume_token is None
        assert result.completed_at == datetime(2026,1,2,tzinfo=timezone.utc)
        assert result.checkpoint_run_id == (explicit if checkpoint == 'explicit' else roots[0])
        assert root.input_json == {'unflushed':'preserve'}
        await db.rollback()
    async with h.factory() as db:
        assert (await db.get(Run, roots[0])).input_json == original_input
