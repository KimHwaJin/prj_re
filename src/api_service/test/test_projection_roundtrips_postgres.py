"""Measure DB work for unchanged persistence results on disposable PostgreSQL.

DTEST_PROJECTION_MEASURE_ONLY=1 allows this same probe on the baseline source;
DTEST_PROJECTION_REPORT writes counters (no SQL parameters/user data).
"""
import asyncio
from contextlib import contextmanager
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from api_service.models.common.message_model import MessageModel
from api_service.models.common.project_model import ProjectModel
from api_service.models.common.session_model import SessionModel
from api_service.models.common.task_model import TaskModel
from api_service.schemas.common.message_schema import MessageCreate, MessageRead
from api_service.services.message_service import MessageService
from api_service.services.agent_run_log_service import AgentRunLogService
from api_service.services.task_service import TaskService
from api_service.test.test_user_identity_postgres import database_url, harness
from api_service.test.test_run_cleanup_postgres import runtime, enqueue

pytestmark = pytest.mark.asyncio


@contextmanager
def count_db(h, name):
    measured = {'sql': [], 'commits': 0}
    def query(conn, cursor, statement, parameters, context, many):
        measured['sql'].append(' '.join(statement.split()))
    def commit(conn):
        measured['commits'] += 1
    event.listen(h.engine.sync_engine, 'after_cursor_execute', query)
    event.listen(h.engine.sync_engine, 'commit', commit)
    try:
        yield measured
    finally:
        event.remove(h.engine.sync_engine, 'after_cursor_execute', query)
        event.remove(h.engine.sync_engine, 'commit', commit)
        if output := os.getenv('DTEST_PROJECTION_REPORT'):
            dest = Path(output)
            report = json.loads(dest.read_text()) if dest.exists() else {}
            report[name] = {'statements': len(measured['sql']), 'commits': measured['commits'],
                            'sql': measured['sql']}
            dest.write_text(json.dumps(report, indent=2)+'\n')


def budget(measured, statements, commits):
    if not os.getenv('DTEST_PROJECTION_MEASURE_ONLY'):
        assert len(measured['sql']) == statements, measured
        assert measured['commits'] == commits, measured


async def uid(h):
    async with h.factory() as db:
        return await db.scalar(select(ProjectModel.user_id).where(ProjectModel.project_id == UUID(h.user['default_project_id'])))


async def test_new_log_returns_generated_values_without_followup_reads(runtime):
    h = runtime
    rid = UUID((await enqueue(h))['id'])
    args = dict(run_id=rid, event_key='perf', agent_name='analysis', node='node', event='result', kind='result', payload={'result':'ok'})
    with count_db(h, 'new_log') as count:
        async with h.factory() as db:
            log = await AgentRunLogService.create(db, **args)
            assert log.created_at and log.log_id and log.payload == args['payload']
    budget(count, statements=6, commits=1)
    with count_db(h, 'existing_log') as count:
        async with h.factory() as db:
            repeated = await AgentRunLogService.create(db, **args)
            assert repeated.log_id == log.log_id
    budget(count, statements=1, commits=0)


async def test_message_response_keeps_db_generated_fields_and_idempotency(runtime):
    h = runtime
    user_id = await uid(h)
    payload = MessageCreate(session_id=UUID(h.session_id), project_id=UUID(h.user['default_project_id']),
                            content_text='saved result', client_request_id=uuid4())
    with count_db(h, 'new_message') as count:
        async with h.factory() as db:
            result = await MessageService.create(db, user_id, payload)
    budget(count, statements=9, commits=1)
    async with h.factory() as db:
        stored = await db.get(MessageModel, result.message.message_id)
        assert MessageRead.model_validate(stored) == result.message
        assert result.message.sequence_no > 0 and result.message.created_at and result.message.updated_at
        assert (await db.get(SessionModel, UUID(h.session_id))).current_leaf_message_id == stored.message_id
    with count_db(h, 'existing_message') as count:
        async with h.factory() as db:
            repeated = await MessageService.create(db, user_id, payload)
    budget(count, statements=7, commits=0)
    assert repeated.message == result.message


async def test_link_graph_task_noop_does_not_commit_and_conflicts_remain_rejected(runtime):
    h = runtime
    rid = UUID((await enqueue(h))['id'])
    graph_id = uuid4()
    with count_db(h, 'new_task_link') as count:
        async with h.factory() as db:
            await TaskService.attach_graph_task_for_run(db, run_id=rid, graph_task_id=graph_id)
    budget(count, statements=2, commits=1)
    with count_db(h, 'existing_task_link') as count:
        async with h.factory() as db:
            await TaskService.attach_graph_task_for_run(db, run_id=rid, graph_task_id=graph_id)
    budget(count, statements=1, commits=0)
    async with h.factory() as db:
        with pytest.raises(RuntimeError, match='already linked'):
            await TaskService.attach_graph_task_for_run(db, run_id=rid, graph_task_id=uuid4())
        await db.rollback()
        assert await db.scalar(select(TaskModel.graph_task_id).where(TaskModel.graph_task_id == graph_id)) == graph_id


async def test_message_dto_also_survives_expiry_enabled_session(runtime):
    h = runtime
    user_id = await uid(h)
    factory = async_sessionmaker(h.engine, expire_on_commit=True, autoflush=False)
    async with factory() as db:
        result = await MessageService.create(db, user_id, MessageCreate(session_id=UUID(h.session_id), content_text='result'))
    assert result.message.sequence_no > 0 and result.message.created_at


async def test_concurrent_message_replay_preserves_one_message_and_session_leaf(runtime):
    h = runtime
    user_id = await uid(h)
    payload = MessageCreate(session_id=UUID(h.session_id), content_text='result', client_request_id=uuid4())
    async def write():
        async with h.factory() as db:
            return await MessageService.create(db, user_id, payload)
    results = await asyncio.wait_for(asyncio.gather(*(write() for _ in range(6))), timeout=10)
    assert len({result.message.message_id for result in results}) == 1
    async with h.factory() as db:
        ids = list(await db.scalars(select(MessageModel.message_id).where(MessageModel.session_id == UUID(h.session_id))))
        assert ids == [results[0].message.message_id]
        assert (await db.get(SessionModel, UUID(h.session_id))).current_leaf_message_id == ids[0]
