"""Real PostgreSQL, one connection, real Run/CRUD paths and gated model waits.

Only DTEST_IDENTITY_TEST_DATABASE_URL's guarded disposable DB is used.
The tiny pool is a regression stress condition, not a production sizing guide.
"""
import asyncio
import time
from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID, uuid4
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt
from sqlalchemy import event, select, text, update
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

import service_settings
import app.agent_run_worker as worker
import app.core.database as database
from app.core.enums import AgentRunStatus, TaskStatus
from app.core.execution_lifecycle import execution_health
from app.models.common.project_model import ProjectModel
from app.models.common.agent_run_model import AgentRunModel
from app.models.common.message_model import MessageModel
from app.models.common.session_model import SessionModel
from app.models.common.agent_run_log_model import AgentRunLogModel
from app.services import agent_graph_service as graphs
from app.services.agent_project_context import read_project_snapshot
from app.services.graph_event_persistence import GraphPersistenceDispatcher, GraphPersistenceResult
from app.test.test_user_identity_postgres import database_url, harness, add_session, headers
from app.test.test_run_cleanup_postgres import runtime, enqueue, rows


@pytest_asyncio.fixture
async def small_pool(runtime, database_url, monkeypatch):
    h = runtime
    old_engine = h.engine
    engine = create_async_engine(database_url, pool_size=1, max_overflow=0, pool_timeout=.3)
    h.engine = engine
    h.factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    h.holds = []
    @event.listens_for(engine.sync_engine, 'checkout')
    def checkout(conn, record, proxy):
        record.info['borrowed_at'] = time.perf_counter()
    @event.listens_for(engine.sync_engine, 'checkin')
    def checkin(conn, record):
        start = record.info.pop('borrowed_at', None)
        if start is not None:
            h.holds.append(time.perf_counter() - start)
    async def request_db():
        async with h.factory() as db:
            yield db
    h.app.dependency_overrides[database.get_db] = request_db
    try:
        yield h
    finally:
        assert engine.pool.checkedout() == 0
        await engine.dispose()
        h.engine = old_engine


async def crud_round(h):
    """Actual create/read/update/delete HTTP endpoints using the same one-slot pool."""
    sid = await add_session(h, h.user)
    base = f'/api/v1/sessions/{sid}'
    hdr = headers(h.user['user_id'])
    response = await h.client.get(base, headers=hdr)
    assert response.status_code == 200, response.text
    response = await h.client.patch(base, headers=hdr, json={'session_name': 'renamed'})
    assert response.status_code == 200, response.text
    response = await h.client.delete(base, headers=hdr)
    assert response.status_code in (200, 204), response.text


@pytest.mark.asyncio
async def test_control_open_read_transaction_exhausts_single_connection(small_pool):
    """Reproduce the underlying mechanism without relying on timing guesses."""
    h = small_pool
    async with h.factory() as held:
        await held.scalar(select(ProjectModel.project_id).limit(1))
        assert held.in_transaction() and h.engine.pool.checkedout() == 1
        with pytest.raises(PoolTimeout):
            async with h.factory() as blocked:
                await blocked.execute(text('SELECT 1'))
    await crud_round(h)


def gated_graph(gate):
    async def model(state):
        gate.entered.append(state['session_id'])
        if len(gate.entered) == gate.count:
            gate.ready.set()
        await gate.release.wait()
        return {**state, 'routing_result': {'route': 'analysis'},
                'task_id': state.get('task_id') or str(uuid4()),
                'messages': [{'role': 'assistant', 'name': 'faq', 'content': 'mock result'}]}
    async def approval(state):
        answer = interrupt({'kind': 'USER_APPROVAL'})
        return {**state, 'approved': answer}
    return (StateGraph(dict).add_node('model', model).add_node('approval', approval)
            .add_node('after_approval', model).add_edge(START, 'model')
            .add_edge('model', 'approval').add_edge('approval', 'after_approval')
            .add_edge('after_approval', END).compile(checkpointer=InMemorySaver()))


async def execute_queued():
    item = await worker.claim_one()
    assert item is not None
    await worker.execute_claimed(item)


@pytest.mark.asyncio
@pytest.mark.parametrize('resume', [False, True])
async def test_two_runs_share_one_connection_and_crud_works_during_model_wait(small_pool, monkeypatch, resume):
    h = small_pool
    gate = SimpleNamespace(entered=[], count=2, ready=asyncio.Event(), release=asyncio.Event())
    graph = gated_graph(gate)
    graph_runtime = graphs.AgentGraphRuntime()
    graph_runtime.override_graph(graph)
    monkeypatch.setattr(graphs, 'runtime', graph_runtime)
    sessions = [h.session_id, await add_session(h, h.user)]
    queued = [await enqueue(h, sid) for sid in sessions]
    if resume:
        gate.release.set()
        await asyncio.gather(*(execute_queued() for _ in queued))
        gate.entered.clear(); gate.ready.clear(); gate.release.clear()
        queued = []
        for sid in sessions:
            response = await h.client.post(f'/api/v1/sessions/{sid}/runs',
                headers={**headers(h.user['user_id']), 'Idempotency-Key': str(uuid4())},
                json={'command': {'approved': True}})
            assert response.status_code == 202, response.text
            queued.append(response.json())
    jobs = [asyncio.create_task(execute_queued()) for _ in queued]
    try:
        await asyncio.wait_for(gate.ready.wait(), 5)
        h.holds.clear()
        begun = time.perf_counter()
        # Keep both LLM stand-ins waiting longer than the .3s pool timeout.
        await asyncio.sleep(.4)
        for _ in range(3):
            await crud_round(h)
        # Include Run state polling and same-session admission protection.
        for sid, q in zip(sessions, queued):
            response = await h.client.get(f"/api/v1/sessions/{sid}/runs/{q['id']}", headers=headers(h.user['user_id']))
            assert response.status_code == 200 and response.json()['status'] == 'running', response.text
        blocked = await h.client.post(f'/api/v1/sessions/{sessions[0]}/runs',
            headers={**headers(h.user['user_id']), 'Idempotency-Key': str(uuid4())},
            json={'input': {'messages': [{'role': 'user', 'content': 'blocked'}]}})
        assert blocked.status_code == 409
        assert all(not job.done() for job in jobs)
        assert h.holds and max(h.holds) < time.perf_counter() - begun
        assert execution_health.healthy
    finally:
        gate.release.set()
        await asyncio.wait_for(asyncio.gather(*jobs), 5)
        await graph_runtime.shutdown()
    for q in queued:
        run, task = await rows(h, q['id'])
        assert run.status == (AgentRunStatus.SUCCESS if resume else AgentRunStatus.INTERRUPTED)
        assert task.status == (TaskStatus.SUCCESS if resume else TaskStatus.WAITING_INPUT)
        assert not task.recovery_required and task.graph_task_id is not None
        async with h.factory() as db:
            assert await db.scalar(select(AgentRunLogModel.log_id).where(AgentRunLogModel.run_id == run.run_id).limit(1))
            assert await db.scalar(select(MessageModel.message_id).where(
                MessageModel.session_id == run.session_id, MessageModel.content_text == 'mock result').limit(1))


@pytest.mark.asyncio
async def test_legacy_resume_releases_snapshot_before_checkpoint_update_and_invoke(small_pool):
    h = small_pool
    async with h.factory() as db:
        session = await db.get(SessionModel, UUID(h.session_id))
        uid, pid = session.user_id, session.project_id
    values = {'session_id': h.session_id, 'project_id': str(pid)}
    async def checkpoint_io(*args, **kwargs):
        assert h.engine.pool.checkedout() == 0
        await crud_round(h)
        return values
    graph = SimpleNamespace(
        aget_state=AsyncMock(return_value=SimpleNamespace(values=values)),
        aupdate_state=AsyncMock(side_effect=checkpoint_io),
        ainvoke=AsyncMock(side_effect=checkpoint_io),
    )
    await graphs.ainvoke_resume(user_id=uid, session_id=UUID(h.session_id), checkpoint_run_id=uuid4(),
        command={'approved': True}, graph=graph, session_factory=h.factory,
        dispatcher=GraphPersistenceDispatcher([]))
    graph.aupdate_state.assert_awaited_once()
    assert h.engine.pool.checkedout() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('fail', [False, True])
async def test_stream_projection_releases_before_next_state_and_rolls_back_failure(small_pool, fail):
    h = small_pool
    async with h.factory() as db:
        session = await db.get(SessionModel, UUID(h.session_id))
        uid, pid = session.user_id, session.project_id
        original = await db.scalar(select(ProjectModel.system_prompt).where(ProjectModel.project_id == pid))
    class Dispatcher:
        async def persist_state_delta(self, db, state, cursor, **kwargs):
            await db.execute(update(ProjectModel).where(ProjectModel.project_id == pid).values(system_prompt='projected'))
            if fail:
                raise ValueError('projection failed')
            return GraphPersistenceResult(cursor, [], [])
    class Graph:
        async def astream(self, value, **kwargs):
            for _ in range(2):
                assert h.engine.pool.checkedout() == 0
                await crud_round(h)
                yield value
    async def consume():
        async for _ in graphs.astream_user_turn(user_id=uid, project_id=pid, session_id=UUID(h.session_id),
                run_id=uuid4(), user_request='stream', graph=Graph(), dispatcher=Dispatcher(), session_factory=h.factory):
            assert h.engine.pool.checkedout() == 0
            # Slow consumer must not retain the projection transaction either.
            await asyncio.sleep(.35)
            await crud_round(h)
    if fail:
        with pytest.raises(ValueError, match='projection failed'):
            await consume()
    else:
        await consume()
    async with h.factory() as db:
        current = await db.scalar(select(ProjectModel.system_prompt).where(ProjectModel.project_id == pid))
        assert current == (original if fail else 'projected')


@pytest.mark.asyncio
async def test_snapshot_access_error_returns_connection(small_pool):
    h = small_pool
    with pytest.raises(ValueError, match='does not belong'):
        await read_project_snapshot(user_id=uuid4(), session_id=UUID(h.session_id), session_factory=h.factory)
    assert h.engine.pool.checkedout() == 0
    await crud_round(h)


@pytest.mark.asyncio
async def test_cancel_api_remains_usable_with_one_connection_while_graph_waits(small_pool, monkeypatch):
    h = small_pool
    gate = SimpleNamespace(entered=[], count=1, ready=asyncio.Event(), release=asyncio.Event())
    graph_runtime = graphs.AgentGraphRuntime()
    graph_runtime.override_graph(gated_graph(gate))
    monkeypatch.setattr(graphs, 'runtime', graph_runtime)
    queued = await enqueue(h)
    job = asyncio.create_task(execute_queued())
    try:
        await asyncio.wait_for(gate.ready.wait(), 5)
        response = await h.client.post(f"/api/v1/sessions/{h.session_id}/runs/{queued['id']}/cancel",
            headers=headers(h.user['user_id']), json={'reason': 'test cancellation'})
        assert response.status_code == 202, response.text
        await asyncio.wait_for(asyncio.shield(job), 5)
        run, task = await rows(h, queued['id'])
        assert run.status == AgentRunStatus.CANCELED and task.status == TaskStatus.CANCELED
        assert not task.recovery_required and execution_health.healthy
        await crud_round(h)
    finally:
        gate.release.set()
        await asyncio.wait_for(job, 5)
        await graph_runtime.shutdown()


@pytest.mark.asyncio
async def test_repeated_cancel_waits_for_projection_rollback_and_returns_connection(small_pool):
    from sqlalchemy.ext.asyncio import AsyncSession
    h = small_pool
    async with h.factory() as db:
        session = await db.get(SessionModel, UUID(h.session_id))
        uid, pid = session.user_id, session.project_id
        original = await db.scalar(select(ProjectModel.system_prompt).where(ProjectModel.project_id == pid))
    writing, closing, release_close = asyncio.Event(), asyncio.Event(), asyncio.Event()
    class ClosingSession(AsyncSession):
        async def close(self):
            closing.set()
            await release_close.wait()
            await super().close()
    factory = async_sessionmaker(h.engine, class_=ClosingSession, expire_on_commit=False)
    class Dispatcher:
        async def persist_state_delta(self, db, *args, **kwargs):
            await db.execute(update(ProjectModel).where(ProjectModel.project_id == pid).values(system_prompt='uncommitted'))
            writing.set()
            await asyncio.Event().wait()
    from app.services.graph_crud_persistence import ainvoke_with_crud_message_persistence
    job = asyncio.create_task(ainvoke_with_crud_message_persistence(
        SimpleNamespace(ainvoke=AsyncMock(return_value={'session_id': h.session_id})), {},
        session_factory=factory, user_id=uid, config={}, dispatcher=Dispatcher(),
    ))
    try:
        await asyncio.wait_for(writing.wait(), 5)
        job.cancel()
        await asyncio.wait_for(closing.wait(), 5)
        job.cancel()
        await asyncio.sleep(.02)
        assert not job.done() and h.engine.pool.checkedout() == 1
    finally:
        release_close.set()
        with pytest.raises(asyncio.CancelledError):
            await job
    assert h.engine.pool.checkedout() == 0
    async with h.factory() as db:
        assert await db.scalar(select(ProjectModel.system_prompt).where(ProjectModel.project_id == pid)) == original
    await crud_round(h)


@pytest.mark.asyncio
@pytest.mark.parametrize('slots', [1, 2, 4])
async def test_dispatcher_fills_slots_without_retaining_connections(small_pool, monkeypatch, slots):
    h = small_pool
    settings = service_settings.get_settings()
    monkeypatch.setattr(service_settings, '_snapshot', replace(settings, api=settings.api.model_copy(update={
        'agent_worker_concurrency': slots, 'agent_worker_poll_interval_seconds': .05,
    })))
    gate = SimpleNamespace(entered=[], count=slots, ready=asyncio.Event(), release=asyncio.Event())
    graph_runtime = graphs.AgentGraphRuntime()
    graph_runtime.override_graph(gated_graph(gate))
    monkeypatch.setattr(graphs, 'runtime', graph_runtime)
    queued = [await enqueue(h, await add_session(h, h.user)) for _ in range(slots + 1)]
    stop = asyncio.Event()
    dispatcher = asyncio.create_task(worker.run_forever(stop_event=stop))
    try:
        await asyncio.wait_for(gate.ready.wait(), 5)
        await crud_round(h)
        async with h.factory() as db:
            statuses = list(await db.scalars(select(AgentRunModel.status).where(
                AgentRunModel.run_id.in_([UUID(q['id']) for q in queued]))))
        assert statuses.count(AgentRunStatus.RUNNING) == slots
        assert statuses.count(AgentRunStatus.PENDING) == 1
        assert len(gate.entered) == slots
    finally:
        stop.set(); gate.release.set()
        await asyncio.wait_for(dispatcher, 5)
        await graph_runtime.shutdown()
    assert len(gate.entered) == slots
    assert execution_health.healthy
    pending, _ = await rows(h, queued[-1]['id'])
    assert pending.status == AgentRunStatus.PENDING
