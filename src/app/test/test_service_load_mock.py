"""Contract tests for the deterministic service load-test LLM boundary."""
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent_config import load_agent_settings
from app.agents.orchestration.dependencies import create_llm_dependencies
from app.graphs.builders.build_analysis_workflow_graph import build_analysis_workflow_graph


def mock_settings(**overrides):
    return load_agent_settings({
        'MODEL_PROVIDER': 'mock', 'EXECUTOR_SUBMIT_ENABLED': 'false',
        'DATA_MOCK': 'true', 'DEMO_ARTIFACTS_ENABLED': 'false',
        **overrides,
    })


def action(state):
    return state['__interrupt__'][0].value['action_requests'][0]['name']


def test_real_graph_reaches_approval_without_constructing_llm():
    settings = mock_settings()
    with patch('app.agents.orchestration.dependencies.create_chat_model', side_effect=AssertionError('LLM must not be constructed')):
        deps = create_llm_dependencies(settings)
        graph = build_analysis_workflow_graph(deps, settings, checkpointer=InMemorySaver())
        session = str(uuid4())
        config = {'configurable': {'thread_id': session}}
        state = graph.invoke({'user_request':'서비스 부하테스트', 'session_id':session,
                              'user_id':str(uuid4()),'project_id':str(uuid4())}, config)
        assert action(state) == 'data_selection'
        state = graph.invoke(Command(resume='mock'), config)
        assert action(state) == 'analysis_context'
        state = graph.invoke(Command(resume={'objective':'EDA'}), config)
        assert action(state) == 'workflow_candidate_selection'
        state = graph.invoke(Command(resume={'candidate_number':1}), config)
        assert action(state) == 'workflow_approval'
        assert state['user_request'] == '서비스 부하테스트'
        assert state['workflow']['workflow']['status'] == 'ready'
        assert len(state['workflow']['workflow']['steps']) >= 3
        assert not state.get('execution_id')


def test_mock_allows_executor_submission_and_rejects_invalid_delay():
    create_llm_dependencies(mock_settings(EXECUTOR_SUBMIT_ENABLED='true'))
    for value in ['-1','60001','nan']:
        with pytest.raises(ValueError):
            mock_settings(MODEL_MOCK_DELAY_MS=value)


def test_real_provider_keeps_existing_model_factory():
    with patch('app.agents.orchestration.dependencies.create_chat_model', side_effect=RuntimeError('real model factory')):
        with pytest.raises(RuntimeError, match='real model factory'):
            create_llm_dependencies(replace(mock_settings(), model_provider='openai_compatible'))


@pytest.mark.asyncio
async def test_approval_submits_contract_and_registers_execution(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from scripts.loadtest import mock_executor

    monkeypatch.setattr(mock_executor, 'DB_PATH', tmp_path / 'submissions.sqlite')
    client = TestClient(mock_executor.app)
    captured = []
    registered = []

    def submit(settings, payload):
        captured.append(payload)
        response = client.post('/api/v1/executions', json=payload)
        assert response.status_code == 202
        return {'status_code': response.status_code, 'body': response.json()}

    class Bindings:
        async def register(self, **kwargs):
            registered.append(kwargs)

    settings = mock_settings(EXECUTOR_SUBMIT_ENABLED='true', EXECUTOR_SOURCE_TYPE='INLINE')
    graph = build_analysis_workflow_graph(create_llm_dependencies(settings), settings,
        checkpointer=InMemorySaver(), bindings=Bindings(), submit_execution_start=submit)
    session = str(uuid4())
    config = {'configurable': {'thread_id': session}}
    state = await graph.ainvoke({'user_request': '서비스 부하테스트', 'session_id': session,
        'user_id': str(uuid4()), 'project_id': str(uuid4())}, config)
    for command in ['mock', {'objective': 'EDA'}, {'candidate_number': 1}, {'approved': True}]:
        state = await graph.ainvoke(Command(resume=command), config)
    boundary = state['__interrupt__'][0].value
    assert boundary['kind'] == 'EXECUTOR_EVENT'
    assert boundary['execution_id'] == str(registered[0]['execution_id'])
    assert registered[0]['session_id'] == session
    assert len(captured) == 1
    assert all(s['payload']['source']['type'] == 'INLINE' for s in captured[0]['operation']['spec']['steps'])
    # A retried submission preserves identity; conflicting keys fail visibly.
    retry = client.post('/api/v1/executions', json=captured[0])
    assert retry.json()['execution_id'] == boundary['execution_id']
    assert client.get('/health').json()['unique_submissions'] == 1
    from copy import deepcopy
    changed = deepcopy(captured[0])
    changed['context']['session_id'] = str(uuid4())
    assert client.post('/api/v1/executions', json=changed).status_code == 409
    assert client.post('/api/v1/executions', json={}).status_code == 422


def test_submit_scenario_requires_execution_confirmation():
    from scripts.loadtest.scenario import STAGES, execute

    for valid in (True, False):
        calls = []
        def request(method, path, **kwargs):
            calls.append((path, kwargs.get('json')))
            if path.endswith('/sessions'):
                return {'id': str(uuid4())}
            index = len(calls) - 2
            interrupt = ({'kind': 'EXECUTOR_EVENT', 'execution_id': str(uuid4())} if valid else
                         {'action_requests': [{'name': 'workflow_approval'}]}) if index == 4 else {
                             'action_requests': [{'name': STAGES[index][0]}]}
            return {'id': str(uuid4()), 'status': 'interrupted', 'interrupt': [interrupt]}
        if valid:
            result = execute(request, {}, 'project', record=lambda *a: None, submit=True)
            assert result['execution_id']
            assert len(result['runs']) == 5
            assert calls[-1][1]['command'] == {'approved': True}
        else:
            with pytest.raises(RuntimeError, match='not confirmed'):
                execute(request, {}, 'project', record=lambda *a: None, submit=True)
