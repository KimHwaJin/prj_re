"""Real compiled graph and approval rules, without external model/Executor calls."""
from copy import deepcopy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from service_settings import load_settings
from service_contracts.plan_review import patch_review, new_review, PlanReviewError
from service_contracts.plan_projection import plan_view
from service_contracts.user_resume import resume_identity, resume_envelope
from service_contracts.run_request import RunRequest
from agent_service.agents.analysis.planning.runtime import PlanningRuntime
from agent_service.agents.analysis.planning.graph import build_planning_graph
from agent_service.agents.analysis.agent_builders.conversation.agent import reply_schema


def setup():
    settings = load_settings(config={'MODEL_PROVIDER': 'mock', 'ANALYSIS_DATASETS': {
        'default-nce': {'title': 'NCE', 'runtime_path': '/workspace/pv/default_data/df_nce_long_format.parquet', 'scope': 'GLOBAL'}
    }}, environ={})
    runtime = PlanningRuntime(settings.agent)
    value = {key: str(uuid4()) for key in ('user_id', 'project_id', 'session_id', 'run_id')}
    value.update(user_request='데이터의 품질과 이상치를 확인해줘', model_selection=runtime.models.select().model_dump(),
                 initial_request_identity={'command_id': value['run_id']})
    return runtime, value, {'configurable': {'thread_id': value['session_id']}}


async def resume(graph, config, state, action):
    command = {'resume': action}
    interrupt = state['__interrupt__'][0]
    identity = resume_identity(str(uuid4()), interrupt.id, command)
    result = await graph.ainvoke(Command(resume={interrupt.id: resume_envelope(identity, command)}), config, durability='sync')
    assert result['user_resume_receipt'] == identity
    return result


@pytest.mark.asyncio
async def test_checkpoint_edit_restart_approve_has_one_model_call_and_frozen_source():
    runtime, value, config = setup()
    saver = InMemorySaver()
    graph = build_planning_graph(runtime, checkpointer=saver)
    state = await graph.ainvoke(value, config, durability='sync')
    assert state['__interrupt__'] and state['initial_request_receipt'] == value['initial_request_identity']
    view = state['plan_views'][0]
    assert view['inputs'][0]['value'] == 'default-nce'
    assert 'code' not in json.dumps(view) and '/workspace' not in json.dumps(view)
    action = {'action': 'edit_plan', 'plan_id': view['plan_id'], 'plan_revision': 1,
              'step_changes': [{'step_id': 'outliers', 'parameter': 'method', 'value': 'iqr'},
                               {'step_id': 'statistics', 'parameter': 'columns', 'value': ['max_val']}]}
    state = await resume(graph, config, state, action)
    assert state['plan_views'][0]['plan_revision'] == 2
    # New graph instance restores the actual checkpoint and consumes no second model call.
    graph = build_planning_graph(runtime, checkpointer=saver)
    action.update(action='approve_plan', plan_revision=2)
    action.pop('step_changes')
    state = await resume(graph, config, state, action)
    assert not state.get('__interrupt__') and state['final_response']['status'] == 'plan_approved'
    frozen = state['approved_snapshot']
    assert frozen['approval_sha256'] and frozen['asset_revision'] == runtime.catalog.revision
    assert frozen['steps'][2]['arguments']['columns']['value'] == ['max_val']
    field = next(p for step in state['final_response']['approved_plan']['steps'] if step['step_id'] == 'statistics' for p in step['parameters'] if p['name'] == 'columns')
    assert field['origin'] == 'user' and field['has_value']
    assert all('code' in item for item in frozen['tool_sources'].values())
    assert len(runtime.agents) == 1 and next(iter(runtime.agents.values())).calls == 1
    assert state['public_events'][0]['envelope']['type'] == 'interaction.resolved'


@pytest.mark.asyncio
async def test_bad_form_rotates_wait_without_reinvoking_model_and_answer_retains_history():
    runtime, value, config = setup()
    graph = build_planning_graph(runtime, checkpointer=InMemorySaver())
    state = await graph.ainvoke(value, config)
    before = state['__interrupt__'][0].id
    view = state['plan_views'][0]
    state = await resume(graph, config, state, {'action': 'edit_plan', 'plan_id': view['plan_id'],
        'plan_revision': 1, 'excluded_step_ids': ['load']})
    assert state['review_error'] and state['__interrupt__'][0].id != before
    assert next(iter(runtime.agents.values())).calls == 1
    value.update(run_id=str(uuid4()), user_request='[answer] 통계가 뭐야?')
    value['initial_request_identity'] = {'command_id': value['run_id']}
    state = await graph.ainvoke(value, config)
    assert state['final_response']['status'] == 'answer' and state['reviews'] == []
    assert len(state['history']) == 4


@pytest.mark.asyncio
async def test_user_can_choose_second_candidate_without_approving_the_first():
    runtime, value, config = setup()
    from importlib.resources import files
    document = json.loads(files('agent_service.agents.analysis.planning').joinpath('fixtures/quality-review.json').read_text())
    second = deepcopy(document)
    second['name'] = '대안 계획'
    second['workflow_id'] = 'alternative_quality'
    reply = reply_schema(runtime.catalog, 5)(kind='plans', message='두 가지 계획을 비교해 주세요.', plans=[
        {'definition': definition, 'input_values': {'dataset':'default-nce'}} for definition in (document, second)])
    async def respond(*args):
        return reply
    runtime.respond = respond
    graph = build_planning_graph(runtime, checkpointer=InMemorySaver())
    state = await graph.ainvoke(value, config)
    assert len(state['plan_views']) == 2
    selected = state['plan_views'][1]
    state = await resume(graph, config, state, {'action':'approve_plan', 'plan_id':selected['plan_id'], 'plan_revision':1})
    assert state['approved_snapshot']['document']['workflow_id'] == 'alternative_quality'
    assert not state['reviews'][0]['consumed'] and state['reviews'][1]['consumed']


def test_dataset_ownership_exclusions_and_revision_are_checked():
    runtime, value, _ = setup()
    from importlib.resources import files
    document = json.loads(files('agent_service.agents.analysis.planning').joinpath('fixtures/quality-review.json').read_text())
    policy = {'allowed_modes': ['MULTI'], 'repair_level_limit': 4, 'max_repair_attempts_limit': 3}
    review = new_review(document, {}, runtime.catalog.metadata, policy)
    action = {'action': 'approve_plan', 'plan_id': review['plan_id'], 'plan_revision': 1}
    with pytest.raises(PlanReviewError, match='missing'):
        patch_review(review, action, datasets=runtime.datasets, context=value)
    action['input_values'] = {'dataset': '/workspace/pv/private.parquet'}
    with pytest.raises(PlanReviewError, match='inaccessible'):
        patch_review(review, action, datasets=runtime.datasets, context=value)
    datasets = {**runtime.datasets, 'private': {'scope': 'PROJECT', 'owner_user_id': str(uuid4()), 'project_id': value['project_id']}}
    action['input_values'] = {'dataset': 'private'}
    with pytest.raises(PlanReviewError, match='inaccessible'):
        patch_review(review, action, datasets=datasets, context=value)
    action['input_values'] = {'dataset': 'default-nce'}
    approved = patch_review(review, action, datasets=datasets, context=value)
    assert approved['consumed'] and not review['consumed']
    with pytest.raises(PlanReviewError, match='already approved'):
        patch_review(approved, action, datasets=datasets, context=value)


def test_request_xor_and_candidate_limit():
    runtime, _, _ = setup()
    with pytest.raises(ValueError):
        RunRequest.model_validate({'command': {'resume': {'action': 'approve_plan', 'plan_id': 'p', 'plan_revision': 1}}})
    with pytest.raises(ValueError):
        RunRequest.model_validate({'input': {'content': [{'type': 'text', 'text': 'hello'}]}, 'run_id': str(uuid4())})
    from importlib.resources import files
    document = json.loads(files('agent_service.agents.analysis.planning').joinpath('fixtures/quality-review.json').read_text())
    with pytest.raises(ValueError):
        reply_schema(runtime.catalog, 1)(kind='plans', message='plans', plans=[{'definition': document}]*2)
    document['steps'][0]['tool_id'] = 'unregistered'
    with pytest.raises(ValueError, match='unregistered tool'):
        reply_schema(runtime.catalog, 5)(kind='plans', message='plans', plans=[{'definition': document}])


def test_central_agent_group_and_placeholder_availability_survive_refresh():
    settings = load_settings(config={'MAX_PLAN_CANDIDATES': 2, 'AGENT_DISCOVERY_MAX_ROUNDS': 3, 'SET_MAX_HISTORY': 10}, environ={'MAX_PLAN_CANDIDATES':'7'})
    assert settings.agent.max_plan_candidates == 2
    assert settings.agent.agent_discovery_max_rounds == 3
    from agent_service.agents.analysis.planning.catalog import AssetCatalog
    from agent_service.agents.analysis.workflow.tools.generate_tool_registry import build_registry
    catalog = AssetCatalog()
    assert 'extract_data' not in catalog.metadata['tools'] and 'data_load' in catalog.metadata['tools']
    registry = build_registry(catalog.root/'tools')
    assert registry['tools']['extract_data']['availability'] == 'test_only'
