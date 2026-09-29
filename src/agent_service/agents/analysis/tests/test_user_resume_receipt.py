"""Production HITL nodes keep private resume identity out of business payloads."""
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from agent_config import load_agent_settings
from agent_service.agents.analysis.dependencies import create_llm_dependencies
from agent_service.agents.analysis.graph import build_analysis_workflow_graph
from service_contracts.user_resume import resume_envelope, resume_identity
from service_contracts.initial_request import initial_identity


@pytest.mark.asyncio
@pytest.mark.parametrize("with_initial_identity", [False, True])
async def test_actual_analysis_hitl_nodes_persist_each_addressed_receipt(tmp_path, with_initial_identity):
    settings = load_agent_settings({'MODEL_PROVIDER': 'mock', 'DATA_MOCK': 'true',
        'DEMO_ARTIFACTS_ENABLED': 'false', 'EXECUTOR_SUBMIT_ENABLED': 'false',
        'EXECUTOR_SOURCE_TYPE': 'INLINE', 'ARTIFACTS_ROOT': str(tmp_path)})
    graph = build_analysis_workflow_graph(create_llm_dependencies(settings), settings,
                                          checkpointer=InMemorySaver())
    config = {'configurable': {'thread_id': str(uuid4())}}
    inputs = {'user_request': 'analysis',
        'session_id': config['configurable']['thread_id'], 'user_id': 'user', 'project_id': 'project'}
    if with_initial_identity:
        inputs['run_id'] = str(uuid4())
        inputs['initial_request_identity'] = initial_identity(inputs)
    result = await graph.ainvoke(inputs, config, durability='sync')
    saved = await graph.aget_state(config)
    if with_initial_identity:
        assert saved.values['run_id'] == inputs['run_id']
        assert saved.values['initial_request_receipt'] == inputs['initial_request_identity']
    else:
        assert saved.values.get('initial_request_receipt') is None
    for answer in ['mock', {'objective': 'EDA'}, {'candidate_number': 1}, {'approved': True}]:
        target = result['__interrupt__'][0].id
        identity = resume_identity(str(uuid4()), target, answer)
        result = await graph.ainvoke(Command(resume={target: resume_envelope(identity, answer)}),
                                    config, durability='sync')
        saved = await graph.aget_state(config)
        assert saved.values['user_resume_receipt'] == identity
    assert result['executor_submit_response']['skipped']
    assert not (await graph.aget_state(config)).next
    # The transport envelope never becomes the user's stored chat content.
    assert '__user_resume_v1__' not in str(result['messages'])
