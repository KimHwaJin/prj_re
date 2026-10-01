"""Default graph projection: real transaction/lock behavior and comparable costs."""
import asyncio
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from api_service.models.common.agent_run_log_model import AgentRunLogModel
from api_service.models.common.agent_run_model import AgentRunModel
from api_service.models.common.message_model import MessageModel
from api_service.models.common.session_model import SessionModel
from api_service.models.common.task_event_model import TaskEventModel
from api_service.models.common.task_model import TaskModel
from api_service.services.graph_crud_persistence import persist_graph_state
from api_service.services.task_event_service import TaskEventService
from api_service.services.message_service import MessageService
from api_service.test.test_projection_roundtrips_postgres import count_db, budget, uid
from api_service.test.test_user_identity_postgres import database_url, harness
from api_service.test.test_run_cleanup_postgres import runtime, enqueue

pytestmark = pytest.mark.asyncio


async def setup_state(h, *, user_message=False):
    rid = UUID((await enqueue(h))['run_id'])
    state = {'session_id': h.session_id, 'project_id': h.user['default_project_id'],
             'run_id': str(rid), 'user_request': 'test request', 'messages': [
                 {'role': 'assistant', 'name': f'answer{i}', 'content': f'result {i}'}
                 for i in range(4)]}
    if user_message:
        state['messages'].insert(0, {'role': 'user', 'content': 'original input'})
    return rid, state, await uid(h)


async def snapshot(h, rid):
    async with h.factory() as db:
        run = await db.get(AgentRunModel, rid)
        task = await db.get(TaskModel, run.task_id)
        session = await db.get(SessionModel, UUID(h.session_id))
        return {
            'messages': [(m.message_id, m.content_text, m.sequence_no) for m in
                         await db.scalars(select(MessageModel).where(MessageModel.session_id == session.session_id).order_by(MessageModel.sequence_no))],
            'logs': [(v.log_id, v.event_key) for v in await db.scalars(select(AgentRunLogModel).where(AgentRunLogModel.run_id == rid).order_by(AgentRunLogModel.event_key))],
            'events': [(v.task_event_id, v.sequence, v.agent_run_log_id) for v in await db.scalars(select(TaskEventModel).where(TaskEventModel.run_id == rid).order_by(TaskEventModel.sequence))],
            'leaf': session.current_leaf_message_id, 'sequence': task.last_event_sequence,
            'run_trigger': run.trigger_message_id, 'task_trigger': task.trigger_message_id,
        }


async def test_batch_query_cost_and_replay_equivalence(runtime):
    h = runtime
    rid, state, user_id = await setup_state(h)
    with count_db(h, 'new_result_batch') as measured:
        await persist_graph_state(state, user_id=user_id, session_factory=h.factory, agent_run_id=rid)
    # SQL budget verified against the baseline with identical inputs.
    budget(measured, statements=44, commits=1)
    first = await snapshot(h, rid)
    assert [v[1] for v in first['messages']] == [f'result {i}' for i in range(4)]
    assert len(first['logs']) == 5
    assert len([e for e in first['events'] if e[2]]) == 5
    assert first['leaf'] == first['messages'][-1][0]
    with count_db(h, 'replayed_result_batch') as measured:
        await persist_graph_state(state, user_id=user_id, session_factory=h.factory, agent_run_id=rid)
    budget(measured, statements=16, commits=1)
    assert await snapshot(h, rid) == first


@pytest.mark.parametrize('cancel', [False, True])
async def test_batch_failure_rolls_back_all_new_output_then_replays(runtime, monkeypatch, cancel):
    h = runtime
    rid, state, user_id = await setup_state(h)
    original = await snapshot(h, rid)
    append = TaskEventService.append_for_run
    calls = 0
    async def fail(db, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise asyncio.CancelledError() if cancel else RuntimeError('injected result save failure')
        return await append(db, **kwargs)
    monkeypatch.setattr(TaskEventService, 'append_for_run', fail)
    with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
        await persist_graph_state(state, user_id=user_id, session_factory=h.factory, agent_run_id=rid)
    assert await snapshot(h, rid) == original
    monkeypatch.setattr(TaskEventService, 'append_for_run', append)
    await persist_graph_state(state, user_id=user_id, session_factory=h.factory, agent_run_id=rid)
    saved = await snapshot(h, rid)
    assert len(saved['messages']) == 4 and len(saved['logs']) == 5
    assert saved['sequence'] == original['sequence'] + 5


async def test_concurrent_same_snapshot_has_one_ordered_output(runtime):
    h = runtime
    rid, state, user_id = await setup_state(h)
    await asyncio.wait_for(asyncio.gather(*(
        persist_graph_state(state, user_id=user_id, session_factory=h.factory, agent_run_id=rid)
        for _ in range(6))), timeout=10)
    saved = await snapshot(h, rid)
    assert len(saved['messages']) == 4 and len(saved['logs']) == 5
    assert [v[1] for v in saved['messages']] == [f'result {i}' for i in range(4)]
    assert len([e for e in saved['events'] if e[2]]) == 5
    assert saved['leaf'] == saved['messages'][-1][0]


@pytest.mark.parametrize('wrong', ['user', 'project', 'session'])
async def test_batch_validates_context_before_any_output(runtime, wrong):
    h = runtime
    rid, state, user_id = await setup_state(h)
    before = await snapshot(h, rid)
    if wrong == 'user':
        user_id = uuid4()
    else:
        state[wrong + '_id'] = str(uuid4())
    with pytest.raises(HTTPException):
        await persist_graph_state(state, user_id=user_id, session_factory=h.factory, agent_run_id=rid)
    assert await snapshot(h, rid) == before


async def test_user_trigger_link_does_not_commit_inside_batch(runtime, monkeypatch):
    h = runtime
    rid, state, user_id = await setup_state(h, user_message=True)
    append = TaskEventService.append_for_run
    async def fail(db, **kwargs):
        if kwargs['payload']['agent_name'] == 'answer1':
            raise RuntimeError('after user trigger link')
        return await append(db, **kwargs)
    original = await snapshot(h, rid)
    monkeypatch.setattr(TaskEventService, 'append_for_run', fail)
    with pytest.raises(RuntimeError, match='after user'):
        await persist_graph_state(state, user_id=user_id, session_factory=h.factory, agent_run_id=rid)
    assert await snapshot(h, rid) == original
    monkeypatch.setattr(TaskEventService, 'append_for_run', append)
    with count_db(h, 'result_with_user_trigger') as measured:
        await persist_graph_state(state, user_id=user_id, session_factory=h.factory, agent_run_id=rid)
    assert measured['commits'] == 1
    saved = await snapshot(h, rid)
    assert saved['run_trigger'] == saved['task_trigger'] == saved['messages'][0][0]


async def test_readers_cannot_see_partial_result(runtime, monkeypatch):
    h = runtime
    rid, state, user_id = await setup_state(h)
    before = await snapshot(h, rid)
    create = MessageService._create_locked
    entered, release = asyncio.Event(), asyncio.Event()
    async def hold(*args, **kwargs):
        result = await create(*args, **kwargs)
        if not entered.is_set():
            entered.set()
            await release.wait()
        return result
    monkeypatch.setattr(MessageService, '_create_locked', hold)
    write = asyncio.create_task(persist_graph_state(state, user_id=user_id, session_factory=h.factory, agent_run_id=rid))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert await snapshot(h, rid) == before
    finally:
        release.set()
        await asyncio.wait_for(write, 5)
    assert len((await snapshot(h, rid))['messages']) == 4


async def test_batch_and_single_log_writer_share_lock_order(runtime):
    from api_service.services.agent_run_log_service import AgentRunLogService
    h = runtime
    rid, state, user_id = await setup_state(h)
    args = dict(run_id=rid, agent_name='answer2', node='answer2', event='message_emitted',
                kind='agent_run_log', payload=state['messages'][2], commit=False)
    async with h.factory() as db:
        # Hold a later log and the Task event counter in a separate writer.
        await AgentRunLogService.create(db, event_key='answer2:message_emitted:2', **args)
        batch = asyncio.create_task(persist_graph_state(state, user_id=user_id, session_factory=h.factory, agent_run_id=rid))
        try:
            await asyncio.sleep(.05)
            assert not batch.done()
            # Opposite log order must not create log-row <-> Task-row deadlock.
            await asyncio.wait_for(AgentRunLogService.create(db,
                **{**args, 'event_key': 'agent_run:started:0', 'node': 'agent_run', 'event': 'started'}), 5)
            await db.commit()
        except BaseException:
            await db.rollback()
            raise
        finally:
            await asyncio.wait_for(batch, 5)
    saved = await snapshot(h, rid)
    assert len(saved['logs']) == 5 and len(saved['messages']) == 4


async def test_batch_context_cannot_survive_commit(runtime):
    from api_service.services.graph_result_batch import GraphResultBatch
    from api_service.services.graph_event_persistence import GraphPersistenceContext, GraphPersistenceCursor, extract_graph_events
    from api_service.schemas.common.message_schema import MessageCreate
    h = runtime
    rid, state, user_id = await setup_state(h)
    context = GraphPersistenceContext.from_state(state, user_id=user_id, agent_run_id=rid)
    events, _ = extract_graph_events(state, GraphPersistenceCursor(), context=context)
    async with h.factory() as db:
        batch = await GraphResultBatch.prepare(db, state, context, events)
        await db.commit()
        with pytest.raises(RuntimeError, match='different session or transaction'):
            await batch.create_message(db, user_id, MessageCreate(session_id=UUID(h.session_id), content_text='stale'))
