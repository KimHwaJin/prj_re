"""Real service/Worker/checkpointer failure boundaries. Disposable local DB only."""
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any, TypedDict
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, func
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from langgraph.graph import StateGraph, START, END
import dtest.settings.loader as service_settings
from dtest.agent_service.runtime.user_resume import record_user_resume, user_interrupt
from dtest.contracts.user_resume import UserResumeState
from dtest.contracts.initial_request import InitialRequestState
from dtest.agent_service.runtime.initial_request import record_initial_request
from dtest.agent_service.runtime.langgraph.checkpointer import create_checkpointer
import dtest.application.runs.runtime as graphs
import dtest.application.runs.persistence.graph as projection
import dtest.worker_service.command_worker as worker
from dtest.contracts.enums import AgentRunStatus
from dtest.application.runs.lifecycle import execution_health
from dtest.infrastructure.database.models.agent_run_model import AgentRunModel
from dtest.infrastructure.database.models.task_event_model import TaskEventModel
from dtest.application.runs.task_events import TaskEventService
from tests.api_service.test_user_identity_postgres import database_url, harness
from tests.api_service.test_run_cleanup_postgres import runtime, enqueue, rows
from tests.api_service.test_public_run_postgres import state, resume, execute

pytestmark = pytest.mark.asyncio


class State(UserResumeState, InitialRequestState, total=False):
    user_id: str
    project_id: str
    session_id: str
    run_id: str
    thread_id: str
    request_id: str
    user_request: str
    agent_run_id: str
    model_selection: dict
    project_system_prompt: str
    task_id: str
    routing_result: dict
    answer1: Any
    answer2: Any


@pytest_asyncio.fixture
async def real_graph(runtime, monkeypatch):
    h = runtime
    settings = service_settings.get_settings()
    monkeypatch.setattr(service_settings, '_snapshot', replace(
        settings, commands=settings.commands.model_copy(update={'agent_worker_max_retries': 1})))
    calls = {'one': 0, 'two': 0, 'reject': False}

    @record_initial_request
    async def start(s):
        return {'routing_result': {'route': 'analysis'}, 'task_id': str(uuid4())}

    @record_user_resume
    async def one(s):
        answer = user_interrupt({'kind': 'USER_APPROVAL', 'stage': 1})
        calls['one'] += 1
        if calls['reject']:
            raise RuntimeError('after consuming input, before node commit')
        return {'answer1': answer}

    @record_user_resume
    async def two(s):
        if calls.get('fail_next'):
            raise RuntimeError('downstream failure after answer receipt')
        answer = user_interrupt({'kind': 'USER_APPROVAL', 'stage': 2})
        calls['two'] += 1
        return {'answer2': answer}

    def build(saver):
        b = StateGraph(State)
        for name, node in [('start', start), ('one', one), ('two', two)]:
            b.add_node(name, node)
        for left, right in [(START, 'start'), ('start', 'one'), ('one', 'two'), ('two', END)]:
            b.add_edge(left, right)
        return b.compile(checkpointer=saver)

    dsn = make_url(settings.database.database_url).set(drivername='postgresql').render_as_string(hide_password=False)
    @asynccontextmanager
    async def context():
        async with create_checkpointer(database_url=dsn, setup_on_start=True,
                                       min_size=1, max_size=2, timeout=2) as saver:
            yield build(saver)

    rt = graphs.AgentGraphRuntime()
    monkeypatch.setattr(rt, '_graph_context', context)
    monkeypatch.setattr(graphs, 'runtime', rt)
    h.review_runtime, h.review_calls = rt, calls
    try:
        yield h
    finally:
        await graphs.runtime.shutdown()


async def snapshot(h, public_id):
    async with graphs.runtime.open_graph() as graph:
        return await graph.aget_state(graphs.graph_config(h.session_id, public_id))


async def waiting(h):
    queued = await enqueue(h)
    await execute()
    current = await state(h, queued['run_id'])
    assert current['status'] == 'waiting_input'
    return current


@pytest.mark.parametrize('failure', ['projection', 'final_event'])
async def test_retry_repairs_saved_result_without_answering_next_question(real_graph, monkeypatch, failure):
    h = real_graph
    current = await waiting(h)
    original_target = (await snapshot(h, current['run_id'])).tasks[0].interrupts[0].id
    answer = {'approved': True, 'marker': 'first-only'}
    assert (await resume(h, current, command=answer)).status_code == 202
    before, _ = await rows(h, current['run_id'])
    assert before.metadata_json['_resume_target'] == original_target
    if failure == 'projection':
        original = projection._persist_state
        async def fail_once(*args, **kwargs):
            monkeypatch.setattr(projection, '_persist_state', original)
            raise SQLAlchemyError('after durable next interrupt')
        monkeypatch.setattr(projection, '_persist_state', fail_once)
    else:
        original = TaskEventService.append_for_run
        async def fail_once(*args, **kwargs):
            if kwargs.get('event_type') == 'task.waiting_input':
                monkeypatch.setattr(TaskEventService, 'append_for_run', staticmethod(original))
                raise SQLAlchemyError('before final state commit')
            return await original(*args, **kwargs)
        monkeypatch.setattr(TaskEventService, 'append_for_run', staticmethod(fail_once))
    await execute()
    run, _ = await rows(h, current['run_id'])
    assert run.status == AgentRunStatus.PENDING
    assert run.failure['stage'] == 'state_projection'
    middle = await snapshot(h, current['run_id'])
    assert middle.values['answer1']['resume']['input_values']['legacy'] == answer and 'answer2' not in middle.values
    assert middle.values['user_resume_receipt']['command_id'] == str(run.run_id)
    assert middle.tasks[0].interrupts[0].value['stage'] == 2
    # Restart the compiled graph/pool before the queue retry: no in-memory receipt.
    old_runtime = graphs.runtime
    await old_runtime.shutdown()
    new_runtime = graphs.AgentGraphRuntime()
    monkeypatch.setattr(new_runtime, '_graph_context', old_runtime._graph_context)
    monkeypatch.setattr(graphs, 'runtime', new_runtime)
    await execute()
    final = await snapshot(h, current['run_id'])
    run, task = await rows(h, current['run_id'])
    assert final.values['answer1']['resume']['input_values']['legacy'] == answer and 'answer2' not in final.values
    assert h.review_calls['one'] == 1 and h.review_calls['two'] == 0
    assert run.attempt_count == 2 and run.status == AgentRunStatus.INTERRUPTED
    assert not task.recovery_required
    public = await state(h, current['run_id'])
    assert public['status'] == 'waiting_input' and public['interrupt'][0]['stage'] == 2
    # A new actual user answer, with a new command identity, can still finish.
    assert (await resume(h, public, command={'marker': 'second-only'})).status_code == 202
    await execute()
    final = await snapshot(h, current['run_id'])
    assert final.values['answer2']['resume']['input_values']['legacy'] == {'marker': 'second-only'}
    assert (await state(h, current['run_id']))['status'] == 'success'


async def test_dispatched_without_receipt_is_not_replayed(real_graph):
    h = real_graph
    current = await waiting(h)
    h.review_calls['reject'] = True
    assert (await resume(h, current)).status_code == 202
    await execute()
    run, _ = await rows(h, current['run_id'])
    assert run.status == AgentRunStatus.PENDING and run.metadata_json['_resume_started']
    await execute()
    run, task = await rows(h, current['run_id'])
    assert h.review_calls['one'] == 1
    assert task.recovery_required
    assert (await state(h, current['run_id']))['status'] == 'recovery_required'


@pytest.mark.parametrize('corruption', ['target', 'missing_legacy_target'])
async def test_wrong_or_missing_target_never_dispatches(real_graph, corruption):
    h = real_graph
    current = await waiting(h)
    async with h.factory() as db:
        origin = await db.get(AgentRunModel, UUID(current['resume_token']))
        origin.metadata_json = {**origin.metadata_json, '_checkpoint_interrupt_id':
                                'wrong' if corruption == 'target' else None}
        await db.commit()
    assert (await resume(h, current)).status_code == 202
    await execute()
    assert h.review_calls['one'] == 0
    assert (await state(h, current['run_id']))['status'] == 'recovery_required'


async def test_final_commit_response_loss_does_not_requeue_or_duplicate_event(real_graph, monkeypatch):
    h = real_graph
    current = await waiting(h)
    assert (await resume(h, current)).status_code == 202
    original = TaskEventService.append_for_run
    async def lose_commit_response(db, **kwargs):
        result = await original(db, **kwargs)
        if kwargs.get('event_type') == 'task.waiting_input':
            monkeypatch.setattr(TaskEventService, 'append_for_run', staticmethod(original))
            await db.commit()
            raise SQLAlchemyError('commit applied, response lost')
        return result
    monkeypatch.setattr(TaskEventService, 'append_for_run', staticmethod(lose_commit_response))
    await execute()
    run, task = await rows(h, current['run_id'])
    assert run.status == AgentRunStatus.INTERRUPTED and run.attempt_count == 1
    assert not task.recovery_required and await worker.claim_one() is None
    async with h.factory() as db:
        count = await db.scalar(select(func.count()).select_from(TaskEventModel).where(
            TaskEventModel.run_id == run.run_id, TaskEventModel.event_type == 'task.waiting_input'))
    assert count == 1 and h.review_calls['one'] == 1


@pytest.mark.parametrize('mode', ['bad_receipt', 'incomplete_graph', 'dispatch_commit_lost', 'retry_exhausted'])
async def test_ambiguous_or_exhausted_recovery_never_becomes_success(real_graph, monkeypatch, mode):
    h = real_graph
    current = await waiting(h)
    if mode == 'incomplete_graph':
        h.review_calls['fail_next'] = True
    elif mode == 'dispatch_commit_lost':
        from dtest.application.runs.protocols import user_resume as boundary
        original = boundary.mark_started
        async def lost(**kwargs):
            await original(**kwargs)
            raise SQLAlchemyError('dispatch marker committed but response lost')
        monkeypatch.setattr(boundary, 'mark_started', lost)
    else:
        original = projection._persist_state
        async def fail_once(*args, **kwargs):
            monkeypatch.setattr(projection, '_persist_state', original)
            raise SQLAlchemyError('projection not saved')
        monkeypatch.setattr(projection, '_persist_state', fail_once)
    if mode == 'retry_exhausted':
        settings = service_settings.get_settings()
        monkeypatch.setattr(service_settings, '_snapshot', replace(settings,
            commands=settings.commands.model_copy(update={'agent_worker_max_retries': 0})))
    assert (await resume(h, current)).status_code == 202
    if mode == 'retry_exhausted':
        await execute()
    else:
        await execute()
        if mode == 'bad_receipt':
            async with graphs.runtime.open_graph() as graph:
                config = graphs.graph_config(h.session_id, current['run_id'])
                saved = await graph.aget_state(config)
                await graph.aupdate_state(config, {'user_resume_receipt': {
                    **saved.values['user_resume_receipt'], 'digest': 'wrong'}})
        await execute()
    assert h.review_calls['one'] == (0 if mode == 'dispatch_commit_lost' else 1)
    assert h.review_calls['two'] == 0
    run, task = await rows(h, current['run_id'])
    assert task.recovery_required and run.status == AgentRunStatus.RUNNING
    assert execution_health.healthy
    assert (await state(h, current['run_id']))['status'] == 'recovery_required'


async def test_quarantined_resume_does_not_stop_other_sessions(real_graph):
    from tests.api_service.test_user_identity_postgres import add_session
    from dtest.infrastructure.database.models.session_execution_model import SessionExecutionModel
    h = real_graph
    current = await waiting(h)
    h.review_calls['reject'] = True
    assert (await resume(h, current)).status_code == 202
    await execute()
    await execute()
    assert (await state(h, current['run_id']))['status'] == 'recovery_required'
    assert execution_health.healthy
    async with h.factory() as db:
        owner = await db.get(SessionExecutionModel, UUID(h.session_id))
        assert owner.token is None  # stopped writer; Task guard remains durable
    sid = await add_session(h, h.user)
    other = await enqueue(h, sid)
    await execute()
    run, task = await rows(h, other['run_id'])
    assert run.status == AgentRunStatus.INTERRUPTED and not task.recovery_required
    assert await worker.claim_one() is None


@pytest.mark.parametrize('failure', ['write_failed', 'guard_rejected'])
async def test_failed_quarantine_retains_process_and_owner_protection(real_graph, monkeypatch, failure):
    from unittest.mock import AsyncMock
    from dtest.contracts.execution import ExecutionNeedsRecovery
    from dtest.application.runs.tasks import TaskService
    from dtest.infrastructure.database.models.session_execution_model import SessionExecutionModel
    h = real_graph
    current = await waiting(h)
    async with h.factory() as db:
        origin = await db.get(AgentRunModel, UUID(current['resume_token']))
        origin.metadata_json = {**origin.metadata_json, '_checkpoint_interrupt_id': 'wrong'}
        await db.commit()
    assert (await resume(h, current)).status_code == 202
    guard = (AsyncMock(side_effect=SQLAlchemyError('recovery write unavailable'))
             if failure == 'write_failed' else AsyncMock(return_value=False))
    monkeypatch.setattr(TaskService, 'require_recovery', guard)
    with pytest.raises(ExecutionNeedsRecovery):
        await execute()
    assert not execution_health.healthy and h.review_calls['one'] == 0
    async with h.factory() as db:
        owner = await db.get(SessionExecutionModel, UUID(h.session_id))
        assert owner.token is not None and owner.recovery_required
