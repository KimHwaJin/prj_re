"""Durable read-schema changes must preserve existing waits and receipts."""
from uuid import UUID, uuid4

import pytest
from langgraph.types import Command

from agent_service.agents.analysis.state import PlanningState
from agent_service.agents.analysis.planning.graph import build_planning_graph
from agent_service.runtime.langgraph.checkpointer import create_checkpointer
from service_contracts.events import EventContext, ExecutorEvent
from service_contracts.user_resume import resume_identity, resume_envelope
from api_service.runs.graph_invocation import GraphInvocation
from api_service.test.test_user_identity_postgres import database_url, harness
from api_service.test.test_async_llm_postgres import checkpoint_url


def full_read_graph(runtime, *, checkpointer):
    """Fixture for previous full-channel reader metadata, not a production option."""
    builder = build_planning_graph(runtime, checkpointer=checkpointer).builder
    for node in builder.nodes.values():
        node.input_schema = PlanningState
    return builder.compile(checkpointer=checkpointer, store=runtime.store)


@pytest.mark.asyncio
@pytest.mark.parametrize('wait', ['plan_review', 'executor', 'decision_review', 'repair_review'])
async def test_full_read_checkpoint_resumes_with_narrow_inputs_after_pool_restart(harness, database_url, tmp_path, monkeypatch, wait):
    from agent_service.agents.analysis.tests import test_agentic_execution as execution
    from agent_service.agents.analysis.tests import test_agentic_repair as repair
    from agent_service.agents.analysis.tests.test_planning_runtime import setup as planning_setup
    dsn = checkpoint_url(database_url)
    async with create_checkpointer(dsn, setup_on_start=True, min_size=1, max_size=2) as saver:
        monkeypatch.setattr(execution, 'InMemorySaver', lambda: saver)
        monkeypatch.setattr(repair, 'InMemorySaver', lambda: saver)
        monkeypatch.setattr(execution, 'build_planning_graph', full_read_graph)
        monkeypatch.setattr(repair, 'build_planning_graph', full_read_graph)
        if wait == 'plan_review':
            runtime, value, config = planning_setup()
            graph = full_read_graph(runtime, checkpointer=saver)
            state = await graph.ainvoke(value, config, durability='sync')
            original_calls = next(iter(runtime.agents.values())).calls
            executor = None
        elif wait == 'repair_review':
            runtime, executor, graph, config, state, calls, deliver, resume = await repair.scenario(tmp_path, monkeypatch, needs_input=True)
            _, state = await deliver(executor.events[0])
        else:
            runtime, executor, graph, config, state, deliver = await execution.setup(tmp_path, monkeypatch)
            if wait == 'decision_review':
                original_role = runtime.execution_role
                async def role(name, *args):
                    result = await original_role(name, *args)
                    if name == 'review':
                        result.needs_user_input = True
                    return result
                monkeypatch.setattr(runtime, 'execution_role', role)
                _, state = await deliver(executor.events[0])
        before = (await graph.aget_state(config)).values
        node_count = len(graph.nodes) - 1
        assert all(len(spec.input_schema.__annotations__) == 77 for spec in graph.builder.nodes.values())
    async with create_checkpointer(dsn, setup_on_start=False, min_size=1, max_size=2) as saver:
        graph = build_planning_graph(runtime, checkpointer=saver)
        restored = await graph.aget_state(config)
        assert restored.values == before
        assert len(graph.nodes) - 1 == node_count
        boundary = restored.tasks[0].interrupts[0]
        if wait == 'plan_review':
            review = before['plan_views'][0]
            command = {'resume': {'action': 'approve_plan', 'plan_id': review['plan_id'], 'plan_revision': review['plan_revision']}}
        elif wait == 'repair_review':
            command = repair.approve(before['repair_review'])
        elif wait == 'decision_review':
            review = before['decision_review']
            command = {'resume': {'action': 'approve_decisions', 'interaction_id': review['interaction_id'], 'revision': review['revision'],
                'values': {field['decision_id']: field['value'] for field in review['payload']['decisions']}}}
        else:
            command = None
        if command is not None:
            identity = resume_identity(str(uuid4()), boundary.id, command)
            state = await graph.ainvoke(Command(resume={boundary.id: resume_envelope(identity, command)}), config, durability='sync')
            assert state['user_resume_receipt'] == identity
        else:
            event = executor.events[0]
            context = EventContext(namespace='test', session_id=config['configurable']['thread_id'], task_id=before['task_id'],
                execution_id=UUID(executor.id), command_id=uuid4(), event=ExecutorEvent.model_validate(event))
            await GraphInvocation(graph, model_validator=None).executor_resume(context)
            state = (await graph.aget_state(config)).values
            assert state['ew_receipts'][str(context.command_id)] == event['event_id']
            count = len(executor.calls)
            await GraphInvocation(graph, model_validator=None).executor_resume(context)
            assert len(executor.calls) == count
        if executor:
            assert state['execution_id'] == before['execution_id']
            assert state['approved_snapshot'] == before['approved_snapshot']
            if wait == 'repair_review':
                assert executor.globals['repair_load_calls'] == 1
                assert state['repair_attempts'] == 1
        else:
            assert state['final_response']['status'] == 'plan_approved'
            assert next(iter(runtime.agents.values())).calls == original_calls
        assert saver.conn.get_stats()['pool_available'] == saver.conn.get_stats()['pool_size']
