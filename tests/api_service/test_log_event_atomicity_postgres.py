"""Real PostgreSQL transaction, replay and concurrent producer guarantees."""
import asyncio
from uuid import UUID

import pytest
from sqlalchemy import select, event as sql_event
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from unittest.mock import AsyncMock

from api_service.models.agent_run_log_model import AgentRunLogModel
from api_service.models.agent_run_model import AgentRunModel
from api_service.models.task_event_model import TaskEventModel
from api_service.models.task_model import TaskModel
from api_service.runs.logs import AgentRunLogService
from api_service.runs.task_events import TaskEventService
from tests.api_service.test_user_identity_postgres import database_url, harness
from tests.api_service.test_run_cleanup_postgres import runtime, enqueue

pytestmark = pytest.mark.asyncio


def arguments(run_id, key='result'):
    return dict(run_id=run_id, event_key=key, agent_name='analysis', node='node',
                event='result', kind='agent_run_log', payload={'synthetic': True})


def event_body(log):
    return {key: getattr(log, key) for key in ('agent_name', 'node', 'event', 'kind', 'payload')}


async def read(h, run_id):
    async with h.factory() as db:
        logs = list((await db.scalars(select(AgentRunLogModel).where(
            AgentRunLogModel.run_id == run_id))).all())
        events = list((await db.scalars(select(TaskEventModel).where(
            TaskEventModel.run_id == run_id, TaskEventModel.event_type == 'agent.event'
        ).order_by(TaskEventModel.sequence))).all())
        sequence = await db.scalar(select(TaskModel.last_event_sequence).join(
            AgentRunModel, AgentRunModel.task_id == TaskModel.task_id).where(AgentRunModel.run_id == run_id))
    return logs, events, sequence


@pytest.mark.parametrize('failure', ['before_event', 'after_event', 'before_commit', 'commit_response', 'cancel'])
async def test_failure_rolls_back_pair_and_sequence_then_replay_converges(runtime, monkeypatch, failure):
    h = runtime
    rid = UUID((await enqueue(h))['run_id'])
    baseline = (await read(h, rid))[2]
    append = TaskEventService.append_for_run
    async with h.factory() as db:
        with monkeypatch.context() as patch:
            if failure in ('before_event', 'after_event', 'cancel'):
                async def broken(*args, **kwargs):
                    if failure != 'before_event':
                        await append(*args, **kwargs)
                    if failure == 'cancel':
                        raise asyncio.CancelledError()
                    raise SQLAlchemyError('injected event storage failure')
                patch.setattr(TaskEventService, 'append_for_run', staticmethod(broken))
            else:
                commit = db.commit
                async def broken_commit():
                    if failure == 'commit_response':
                        await commit()
                    raise SQLAlchemyError('injected commit failure')
                patch.setattr(db, 'commit', broken_commit)
            with pytest.raises(asyncio.CancelledError if failure == 'cancel' else SQLAlchemyError):
                await AgentRunLogService.create(db, **arguments(rid))
        logs, events, sequence = await read(h, rid)
        committed = int(failure == 'commit_response')
        assert len(logs) == len(events) == committed
        assert sequence == baseline + committed
        # Same session is usable after failure; no caller-owned cleanup is needed.
        await AgentRunLogService.create(db, **arguments(rid))
    logs, events, sequence = await read(h, rid)
    assert len(logs) == len(events) == 1 and sequence == baseline + 1
    assert events[0].agent_run_log_id == logs[0].log_id
    assert events[0].payload == event_body(logs[0])


async def test_log_flush_failure_never_writes_event(runtime, monkeypatch):
    h = runtime
    rid = UUID((await enqueue(h))['run_id'])
    invalid = {**arguments(rid), 'node': 'x' * 101}
    append = AsyncMock()
    with monkeypatch.context() as patch:
        patch.setattr(TaskEventService, 'append_for_run', append)
        async with h.factory() as db:
            with pytest.raises(SQLAlchemyError):
                await AgentRunLogService.create(db, **invalid)
    append.assert_not_awaited()
    assert not (await read(h, rid))[0]
    async with h.factory() as db:
        await AgentRunLogService.create(db, **arguments(rid))
    assert len((await read(h, rid))[1]) == 1


@pytest.mark.parametrize('distinct', [False, True])
async def test_concurrent_producers_preserve_key_identity_not_payload_identity(runtime, distinct):
    h = runtime
    rid = UUID((await enqueue(h))['run_id'])
    baseline = (await read(h, rid))[2]
    async def write(index):
        async with h.factory() as db:
            return (await AgentRunLogService.create(db, **arguments(rid, str(index) if distinct else 'same'))).log_id
    ids = await asyncio.wait_for(asyncio.gather(*(write(i) for i in range(8))), timeout=10)
    logs, events, sequence = await read(h, rid)
    expected = 8 if distinct else 1
    assert len(set(ids)) == len(logs) == len(events) == expected
    assert len({event.agent_run_log_id for event in events}) == expected
    assert [event.sequence for event in events] == list(range(baseline + 1, baseline + 1 + expected))
    assert sequence == baseline + expected


async def test_existing_log_repairs_from_stored_payload_and_replays_once(runtime):
    h = runtime
    rid = UUID((await enqueue(h))['run_id'])
    async with h.factory() as db:
        log = AgentRunLogModel(**arguments(rid))
        db.add(log)
        await db.commit()  # Emulate old code's incomplete durable log.
        log_id = log.log_id
    for _ in range(2):
        async with h.factory() as db:
            repaired = await AgentRunLogService.create(db, **{**arguments(rid), 'payload': {'changed': True}, 'node': 'changed'})
            assert repaired.log_id == log_id
    logs, events, _ = await read(h, rid)
    assert len(logs) == len(events) == 1
    assert events[0].payload == event_body(logs[0])
    assert events[0].payload['payload'] == {'synthetic': True}
    async with h.factory() as db:
        public_events = await TaskEventService.list_after_public_run(db, run_id=rid, sequence=0, limit=100)
        assert [event.task_event_id for event in public_events if event.event_type == 'agent.event'] == [events[0].task_event_id]


async def test_no_task_keeps_log_only_and_later_attachment_can_repair(runtime):
    h = runtime
    rid = UUID((await enqueue(h))['run_id'])
    async with h.factory() as db:
        run = await db.get(AgentRunModel, rid)
        task_id = run.task_id
        run.task_id = None
        await db.commit()
        await AgentRunLogService.create(db, **arguments(rid))
    logs, events, _ = await read(h, rid)
    assert len(logs) == 1 and not events
    async with h.factory() as db:
        run = await db.get(AgentRunModel, rid)
        run.task_id = task_id
        await db.commit()
        await AgentRunLogService.create(db, **arguments(rid))
    assert len((await read(h, rid))[1]) == 1


async def test_database_unique_link_rejects_duplicate_event_without_consuming_sequence(runtime):
    h = runtime
    rid = UUID((await enqueue(h))['run_id'])
    async with h.factory() as db:
        log = await AgentRunLogService.create(db, **arguments(rid))
        log_id = log.log_id
    before = (await read(h, rid))[2]
    async with h.factory() as db:
        with pytest.raises(IntegrityError):
            await TaskEventService.append_for_run(db, run_id=rid, event_type='agent.event',
                payload={}, agent_run_log_id=log_id)
        await db.rollback()
    logs, events, sequence = await read(h, rid)
    assert len(logs) == len(events) == 1 and sequence == before


async def test_complete_pair_replay_is_read_only_and_keeps_original_content(runtime):
    h = runtime
    rid = UUID((await enqueue(h))['run_id'])
    async with h.factory() as db:
        original = await AgentRunLogService.create(db, **arguments(rid))
        log_id = original.log_id
    statements = []
    def capture(conn, cursor, statement, params, context, many):
        statements.append(statement.strip().upper())
    sql_event.listen(h.engine.sync_engine, 'before_cursor_execute', capture)
    try:
        async with h.factory() as db:
            repeated = await AgentRunLogService.create(db, **{**arguments(rid), 'payload': {'changed': True}})
            assert repeated.log_id == log_id and repeated.payload == {'synthetic': True}
    finally:
        sql_event.remove(h.engine.sync_engine, 'before_cursor_execute', capture)
    assert len(statements) == 1 and statements[0].startswith('SELECT')
    assert 'FOR UPDATE' not in statements[0]
