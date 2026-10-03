"""New requests release old execution state; resumes preserve durable evidence."""
from copy import deepcopy
from uuid import uuid4

import pytest

from agent_service.agents.analysis.planning.graph import build_planning_graph
from agent_service.agents.analysis.planning.lifecycle import new_request_defaults
from agent_service.agents.analysis.state import (
    NODE_INPUTS, PlanningState, PresentationState, PlanningReviewState,
    ExecutionState, RepairState, DeliveryState,
)
from agent_service.agents.analysis.agent_builders.conversation.agent import reply_schema
from agent_service.agents.analysis.tests.test_session_analysis_context import completed_analysis
from agent_service.agents.analysis.tests.test_agentic_execution import setup


def test_every_run_channel_has_a_fresh_reset_and_inputs_do_not_add_channels():
    # Adding a Run field without deciding its next-request lifecycle is an error.
    run_fields = set().union(*(schema.__annotations__ for schema in (
        PresentationState, PlanningReviewState, ExecutionState, RepairState, DeliveryState,
    )))
    first, second = new_request_defaults(), new_request_defaults()
    assert set(first) == run_fields
    for key, value in first.items():
        if isinstance(value, (list, dict)):
            assert value is not second[key]
    for schema in NODE_INPUTS.values():
        assert set(schema.__annotations__) < set(PlanningState.__annotations__)
        assert all(PlanningState.__annotations__[key] == value for key, value in schema.__annotations__.items())


@pytest.mark.asyncio
@pytest.mark.parametrize('failed', [False, True])
async def test_next_faq_clears_previous_payload_errors_operation_ids_and_preserves_session_evidence(tmp_path, monkeypatch, failed):
    if failed:
        runtime, executor, graph, config, state, deliver = await setup(tmp_path, monkeypatch, single=True, missing=True)
        _, state = await deliver(executor.events[0])
        _, state = await deliver(executor.event('execution.completed', {'status': 'FAILED', 'error': {'message': 'File missing'}}))
    else:
        runtime, executor, graph, config, state = await completed_analysis(tmp_path, monkeypatch)
    old = deepcopy(state)
    assert old['execution_command'] and old['approved_snapshot'] and old['ew_receipts']
    assert old['executor_operation_id'] and old['execution_phase'] == 'report'
    if failed:
        assert old['execution_error']
    executor_calls = len(executor.calls)
    seen = []

    async def answer(value, context, datasets):
        # LangGraph, rather than a prompt convention, restricts the role's reads.
        assert not {'execution_command', 'approved_snapshot', 'observations', 'ew_pending', 'repair_candidate'} & value.keys()
        assert value['history'][-1]['content'] == '방금 결과를 설명해줘'
        assert context.session_analysis_context == old['last_analysis_context']
        seen.append(deepcopy(value))
        return reply_schema(runtime.catalog, 5)(kind='answer', message='실제 근거를 설명합니다.', plans=[])

    monkeypatch.setattr(runtime, 'respond', answer)
    graph = build_planning_graph(runtime, checkpointer=graph.checkpointer)
    new_id = str(uuid4())
    following = await graph.ainvoke({**{k: old[k] for k in ('user_id', 'project_id', 'session_id', 'model_selection')},
        'run_id': new_id, 'user_request': '방금 결과를 설명해줘', 'initial_request_identity': {'command_id': new_id}}, config, durability='sync')
    assert len(seen) == 1 and len(executor.calls) == executor_calls
    assert following['initial_request_receipt'] == {'command_id': new_id}
    assert following['last_analysis_context'] == old['last_analysis_context']
    assert following['history'][:len(old['history'])] == old['history']
    assert following['kernel_profile'] == old['kernel_profile']
    assert following['task_id'] != old['task_id'] and following['public_run_id'] == new_id
    for key in ('execution_command', 'executor_operation_id', 'executor_wait_phase', 'execution_phase',
                'execution_error', 'executor_version', 'submitted_steps', 'planning_activity_id',
                'approved_snapshot', 'execution_snapshot', 'ew_pending', 'ew_receipts', 'ew_sequences'):
        assert following[key] == new_request_defaults()[key], key
    # Old checkpoints remain inspectable and retain the exact submitted source.
    history = [s async for s in graph.aget_state_history(config)]
    assert any(s.values.get('execution_command') == old['execution_command'] and
               s.values.get('approved_snapshot') == old['approved_snapshot'] for s in history)


@pytest.mark.asyncio
async def test_wait_and_completion_keep_original_payload_receipts_for_projection_recovery(tmp_path, monkeypatch):
    runtime, executor, graph, config, state, deliver = await setup(tmp_path, monkeypatch, single=True)
    before = deepcopy(state)
    assert before['execution_command']['operation']['spec']['steps']
    graph = build_planning_graph(runtime, checkpointer=graph.checkpointer)
    restored = (await graph.aget_state(config)).values
    assert restored['execution_command'] == before['execution_command']
    assert restored['approved_snapshot'] == before['approved_snapshot']
    _, after = await deliver(executor.events[0])
    ctx, after = await deliver(executor.event('execution.completed', {'status': 'SUCCEEDED', 'error': None}))
    assert after['execution_command'] == before['execution_command']
    assert after['approved_snapshot'] == before['approved_snapshot']
    assert after['user_resume_receipt'] == before['user_resume_receipt']
    assert after['ew_receipts'][str(ctx.command_id)] == str(ctx.event.event_id)
    assert after['observations'] and after['final_response']['status'] == 'analysis_completed'
