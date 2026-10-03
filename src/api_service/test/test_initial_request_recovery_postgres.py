"""Initial input and service projection recovery using real PostgreSQL checkpoints."""
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any
from uuid import UUID, uuid4
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from langgraph.graph import StateGraph, START, END
from sqlalchemy import select, func
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

import service_settings
from agent_service.runtime.initial_request import record_initial_request
from agent_service.runtime.user_resume import record_user_resume, user_interrupt
from agent_service.runtime.langgraph.checkpointer import create_checkpointer
from service_contracts.initial_request import InitialRequestState
from service_contracts.user_resume import UserResumeState
from service_contracts.execution import ExecutionNeedsRecovery
from api_service.core.enums import AgentRunStatus
from api_service.core.execution_lifecycle import execution_health
from api_service.models.common.agent_run_model import AgentRunModel
from api_service.models.common.task_event_model import TaskEventModel
from api_service.models.common.session_execution_model import SessionExecutionModel
from api_service.services.task_event_service import TaskEventService
from api_service.services import agent_graph_service as graphs
from api_service.runs.graph_invocation import GraphInvocation
from api_service.runs.protocols import initial
from api_service.services import graph_crud_persistence as projection
import api_service.agent_run_worker as worker
from api_service.test.test_user_identity_postgres import database_url, harness, headers, add_session
from api_service.test.test_run_cleanup_postgres import runtime, enqueue, rows
from api_service.test.test_public_run_postgres import state, resume, execute, path

pytestmark = pytest.mark.asyncio


class State(InitialRequestState, UserResumeState, total=False):
    user_id: str
    project_id: str
    session_id: str
    thread_id: str
    request_id: str
    user_request: str
    agent_run_id: str
    model_selection: dict
    project_system_prompt: str
    project_prompt_version: int
    trigger_message_id: str
    task_id: str
    routing_result: dict
    answer: Any


@pytest_asyncio.fixture
async def real_initial(runtime, monkeypatch):
    h = runtime
    settings = service_settings.get_settings()
    monkeypatch.setattr(service_settings, '_snapshot', replace(
        settings, api=settings.api.model_copy(update={'agent_worker_max_retries': 1})))
    h.calls = {'entry': 0, 'model': 0, 'terminal': False, 'fail': None}

    @record_initial_request
    async def entry(s):
        h.calls['entry'] += 1
        if h.calls['fail'] == 'entry':
            raise RuntimeError('entry failed before receipt')
        return {'routing_result': {'route': 'analysis'}, 'task_id': str(uuid4())}

    async def model(s):
        h.calls['model'] += 1
        if h.calls['fail'] == 'model':
            raise RuntimeError('model failed after entry receipt')
        return {}

    @record_user_resume
    async def approval(s):
        return {'answer': user_interrupt({'kind': 'USER_APPROVAL', 'stage': 1})}

    def build(saver):
        b = StateGraph(State)
        for name, node in [('entry', entry), ('model', model), ('approval', approval)]:
            b.add_node(name, node)
        b.add_edge(START, 'entry').add_edge('entry', 'model')
        b.add_conditional_edges('model', lambda s: END if h.calls['terminal'] else 'approval')
        b.add_edge('approval', END)
        return b.compile(checkpointer=saver)

    dsn = make_url(settings.api.database_url).set(drivername='postgresql').render_as_string(hide_password=False)
    @asynccontextmanager
    async def context():
        async with create_checkpointer(database_url=dsn, setup_on_start=True,
                                       min_size=1, max_size=2, timeout=2) as saver:
            yield build(saver)
    rt = graphs.AgentGraphRuntime()
    monkeypatch.setattr(rt, '_graph_context', context)
    monkeypatch.setattr(graphs, 'runtime', rt)
    h.graph_context = context
    try:
        yield h
    finally:
        await graphs.runtime.shutdown()


async def saved(h, run_id):
    async with graphs.runtime.open_graph() as graph:
        return await graph.aget_state(graphs.graph_config(h.session_id, run_id))


@pytest.mark.parametrize('failure', ['projection', 'final_event', 'graph_response', 'log_event'])
@pytest.mark.parametrize('terminal', [False, True])
async def test_initial_retry_only_projects_after_runtime_restart(real_initial, monkeypatch, failure, terminal):
    h = real_initial
    h.calls['terminal'] = terminal
    queued = await enqueue(h)
    if failure == 'projection':
        original = projection._persist_state
        async def once(*args, **kwargs):
            monkeypatch.setattr(projection, '_persist_state', original)
            raise SQLAlchemyError('service projection unavailable')
        monkeypatch.setattr(projection, '_persist_state', once)
    elif failure == 'log_event':
        original = TaskEventService.append_for_run
        async def once(*args, **kwargs):
            result = await original(*args, **kwargs)
            if kwargs['event_type'] == 'agent.event':
                monkeypatch.setattr(TaskEventService, 'append_for_run', staticmethod(original))
                raise SQLAlchemyError('event inserted but transaction not committed')
            return result
        monkeypatch.setattr(TaskEventService, 'append_for_run', staticmethod(once))
    elif failure == 'final_event':
        original = TaskEventService.append_for_run
        async def once(*args, **kwargs):
            if kwargs['event_type'] in ('task.waiting_input', 'task.success'):
                monkeypatch.setattr(TaskEventService, 'append_for_run', staticmethod(original))
                raise SQLAlchemyError('final transaction unavailable')
            return await original(*args, **kwargs)
        monkeypatch.setattr(TaskEventService, 'append_for_run', staticmethod(once))
    else:
        async with graphs.runtime.open_graph() as graph:
            original = graph.ainvoke
            async def once(*args, **kwargs):
                result = await original(*args, **kwargs)
                monkeypatch.setattr(graph, 'ainvoke', original)
                raise SQLAlchemyError('graph result lost after checkpoint')
            monkeypatch.setattr(graph, 'ainvoke', once)
    await execute()
    run, _ = await rows(h, queued['run_id'])
    assert run.status == AgentRunStatus.PENDING and run.metadata_json['_initial_started']
    snapshot = await saved(h, queued['run_id'])
    assert snapshot.values['initial_request_receipt']['command_id'] == str(run.run_id)
    if failure != 'graph_response':
        assert run.failure['code'] == 'INITIAL_PROJECTION_FAILED'
    await graphs.runtime.shutdown()
    rt = graphs.AgentGraphRuntime()
    monkeypatch.setattr(rt, '_graph_context', h.graph_context)
    monkeypatch.setattr(graphs, 'runtime', rt)
    # Recovery must not reload mutable project prompts or invoke the model.
    monkeypatch.setattr(GraphInvocation, 'project_snapshot', AsyncMock(side_effect=AssertionError('project reloaded')))
    await execute()
    run, task = await rows(h, queued['run_id'])
    assert run.attempt_count == 2 and not task.recovery_required
    assert h.calls['entry'] == h.calls['model'] == 1
    if failure == 'log_event':
        from api_service.models.common.agent_run_log_model import AgentRunLogModel
        async with h.factory() as db:
            logs = list((await db.scalars(select(AgentRunLogModel).where(AgentRunLogModel.run_id == run.run_id))).all())
            events = list((await db.scalars(select(TaskEventModel).where(
                TaskEventModel.run_id == run.run_id, TaskEventModel.event_type == 'agent.event'))).all())
            assert len(logs) == len(events) == 1
            assert events[0].agent_run_log_id == logs[0].log_id
    public = await state(h, queued['run_id'])
    assert public['status'] == ('success' if terminal else 'waiting_input')
    assert 'answer' not in (await saved(h, queued['run_id'])).values
    if not terminal:
        assert (await resume(h, public, command={'approved': True})).status_code == 202
        await execute()
        assert (await state(h, queued['run_id']))['status'] == 'success'
        assert h.calls['entry'] == h.calls['model'] == 1


@pytest.mark.parametrize('failure', ['entry', 'model', 'marker_response', 'legacy', 'receipt_mismatch', 'budget'])
async def test_uncertain_initial_never_repeats_entry_or_model(real_initial, monkeypatch, failure):
    h = real_initial
    queued = await enqueue(h)
    if failure in ('entry', 'model'):
        h.calls['fail'] = failure
    elif failure == 'marker_response':
        original = initial.mark_started
        async def once(**kwargs):
            await original(**kwargs)
            raise SQLAlchemyError('dispatch marker response lost')
        monkeypatch.setattr(initial, 'mark_started', once)
    elif failure == 'legacy':
        async with h.factory() as db:
            run = await db.get(AgentRunModel, UUID(queued['run_id']))
            run.metadata_json = {k:v for k,v in run.metadata_json.items() if k != '_initial_protocol'}
            await db.commit()
    else:
        monkeypatch.setattr(projection, '_persist_state', AsyncMock(side_effect=SQLAlchemyError('projection')))
        if failure == 'budget':
            settings = service_settings.get_settings()
            monkeypatch.setattr(service_settings, '_snapshot', replace(settings,
                api=settings.api.model_copy(update={'agent_worker_max_retries': 0})))
    await execute()
    if failure not in ('legacy', 'budget'):
        if failure == 'receipt_mismatch':
            async with graphs.runtime.open_graph() as graph:
                cfg = graphs.graph_config(h.session_id, queued['run_id'])
                snapshot = await graph.aget_state(cfg)
                await graph.aupdate_state(cfg, {'initial_request_receipt': {
                    **snapshot.values['initial_request_receipt'], 'digest': 'wrong'}})
        await execute()
    run, task = await rows(h, queued['run_id'])
    assert task.recovery_required and execution_health.healthy
    assert h.calls['entry'] == (0 if failure in ('legacy','marker_response') else 1)
    assert h.calls['model'] <= 1
    assert (await state(h, queued['run_id']))['status'] == 'recovery_required'
    assert await worker.claim_one() is None


async def test_final_commit_response_loss_has_one_terminal_event(real_initial, monkeypatch):
    h = real_initial
    h.calls['terminal'] = True
    queued = await enqueue(h)
    original = TaskEventService.append_for_run
    async def once(db, **kwargs):
        result = await original(db, **kwargs)
        if kwargs['event_type'] == 'task.success':
            monkeypatch.setattr(TaskEventService, 'append_for_run', staticmethod(original))
            await db.commit()
            raise SQLAlchemyError('committed but response lost')
        return result
    monkeypatch.setattr(TaskEventService, 'append_for_run', staticmethod(once))
    await execute()
    run, task = await rows(h, queued['run_id'])
    assert run.status == AgentRunStatus.SUCCESS and not task.recovery_required
    assert run.attempt_count == 1 and await worker.claim_one() is None
    async with h.factory() as db:
        assert await db.scalar(select(func.count()).select_from(TaskEventModel).where(
            TaskEventModel.run_id == run.run_id, TaskEventModel.event_type == 'task.success')) == 1


async def test_new_run_in_same_session_is_not_confused_with_previous_receipt(real_initial):
    h = real_initial
    first = await enqueue(h)
    await execute()
    response = await h.client.post(path(h, first['run_id'])+'/cancel', headers=headers(h.user['user_id']), json={})
    assert response.status_code == 202
    second = await enqueue(h)
    await execute()
    assert (await state(h, second['run_id']))['status'] == 'waiting_input'
    assert (await saved(h, second['run_id'])).values['initial_request_receipt']['command_id'] == second['run_id']
    assert h.calls['entry'] == h.calls['model'] == 2


async def test_failed_error_recording_keeps_owner_and_stops_new_claims(real_initial, monkeypatch):
    h = real_initial
    h.calls['fail'] = 'model'
    queued = await enqueue(h)
    original = TaskEventService.append_for_run
    async def fail_retry(*args, **kwargs):
        if kwargs['event_type'] == 'task.retry_scheduled':
            raise SQLAlchemyError('cannot persist retry')
        return await original(*args, **kwargs)
    monkeypatch.setattr(TaskEventService, 'append_for_run', staticmethod(fail_retry))
    with pytest.raises(ExecutionNeedsRecovery):
        await execute()
    assert not execution_health.healthy
    async with h.factory() as db:
        owner = await db.get(SessionExecutionModel, UUID(h.session_id))
        assert owner.token is not None and owner.recovery_required
