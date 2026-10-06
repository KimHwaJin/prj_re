"""Common planning/execution contracts must survive replacement of the whole asset pool."""
from copy import deepcopy
import json
from uuid import UUID, uuid4

import pytest
import yaml
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from dtest.agent_service.agents.analysis.planning.catalog import AssetCatalog
from dtest.agent_service.agents.analysis.planning.runtime import PlanningRuntime
from dtest.agent_service.agents.analysis.planning.graph import build_planning_graph
from dtest.agent_service.agents.analysis.agent_builders.conversation.agent import reply_schema
from dtest.agent_service.agents.analysis.workflow.tools.generate_tool_registry import build_registry, write_registry
from tests.agent_service.asset_fixtures import assets, definition
from tests.agent_service.test_agentic_execution import LocalExecutor, Bindings
from dtest.application.runs.graph_invocation import GraphInvocation
from dtest.contracts.events import EventContext, ExecutorEvent
from dtest.contracts.plan_review import new_review, patch_review, freeze_approval, PlanReviewError
from dtest.contracts.plan_projection import plan_view
from dtest.contracts.tool_bindings import parameter_bindings, inherit_parameter_policy
from dtest.contracts.user_resume import resume_identity, resume_envelope
from dtest.contracts.workflow_validation import validate
from dtest.settings.loader import load_settings

POLICY = {'allowed_modes': ['MULTI'], 'repair_level_limit': 4, 'max_repair_attempts_limit': 3}
CONTEXT = {'user_id': 'u', 'project_id': 'p', 'session_id': 's', 'public_run_id': 'r'}


@pytest.mark.parametrize('name', ['inventory', 'billing'])
def test_ast_only_metadata_multiple_functions_and_returns_with_completely_different_assets(tmp_path, name):
    catalog, case = assets(tmp_path, name)
    assert set(catalog.sources) == {case['reader'], case['processor']}
    assert list(catalog.metadata['skills']) == [case['skill']]
    assert list(catalog.metadata['tools'][case['reader']]['returns']['outputs']) == [case['selector']]
    metadata = catalog.metadata_tools()[0].invoke({'skill_id': case['skill']})
    assert case['reader'] in metadata['tools'] and all('code' not in tool for tool in metadata['tools'].values())
    assert 'import json' in catalog.sources[case['reader']]['code']
    assert '"""' not in catalog.sources[case['reader']]['code']
    # Filename is different from both functions; module has a forbidden side effect.
    assert set(build_registry(tmp_path / 'tools')['tools']) == set(catalog.sources)


@pytest.mark.parametrize('name', ['inventory', 'billing'])
def test_registered_binding_policies_reject_literals_wrong_kind_and_object_editors(tmp_path, name):
    catalog, case = assets(tmp_path, name)
    original = definition(case)
    assert not validate(original, catalog.metadata)
    doc = deepcopy(original)
    doc['steps'][0]['arguments'][case['reader_arg']] = {'source': 'literal', 'value': '/private/unknown.json'}
    assert any('binding source literal is not allowed' in e for e in validate(doc, catalog.metadata))
    doc = deepcopy(original); doc['inputs']['payload']['kind'] = 'data_reference'
    assert any('kind=parameter' in e for e in validate(doc, catalog.metadata))
    doc = deepcopy(original); doc['steps'][1]['arguments'].pop(case['object_arg'])
    assert any('requires an explicit binding' in e for e in validate(doc, catalog.metadata))
    doc = deepcopy(original)
    doc['steps'][0]['parameter_controls'] = {case['reader_arg']: {'editable': True, 'value_schema': True}}
    errors = validate(doc, catalog.metadata)
    assert any('Edit inputs.payload.editable instead.' in e and 'actual source=workflow_input' in e for e in errors)


@pytest.mark.parametrize('name', ['inventory', 'billing'])
def test_optional_workflow_input_cannot_bypass_required_tool_binding_at_approval(tmp_path, name):
    catalog, case = assets(tmp_path, name)
    doc = definition(case); doc['inputs']['payload']['required'] = False
    review = new_review(doc, {}, catalog.metadata, POLICY)
    with pytest.raises(PlanReviewError, match='Required Tool input is missing'):
        patch_review(review, {'action': 'approve_plan', 'plan_id': review['plan_id'], 'plan_revision': 1}, datasets={}, context=CONTEXT)
    assert not review['consumed']


@pytest.mark.parametrize('name', ['inventory', 'billing'])
@pytest.mark.parametrize('exclude', [False, True])
@pytest.mark.asyncio
async def test_alternate_pool_graph_hitl_edit_condition_real_function_code_and_report(tmp_path, monkeypatch, name, exclude):
    catalog, case = assets(tmp_path / 'assets', name)
    settings = load_settings(config={'MODEL_PROVIDER': 'mock', 'EXECUTOR_SOURCE_TYPE': 'INLINE',
        'EXECUTOR_RUNTIME_PROFILE': 'default', 'EXECUTOR_SHARED_RESULT_ROOT': str(tmp_path),
        'EXECUTOR_BASE_URL': 'http://test', 'EXECUTOR_SUBMIT_ENABLED': True}, environ={}).agent
    executor = LocalExecutor(tmp_path)
    runtime = PlanningRuntime(settings, catalog=catalog, executor=executor, bindings=Bindings())
    async def respond(*args):
        # Deterministic model fixture; actual source functions still execute.
        return reply_schema(catalog, 1)(kind='plans', message='Independent plan',
            plans=[{'definition': definition(case), 'input_values': {'payload': '[2,4,6]'}}])
    monkeypatch.setattr(runtime, 'respond', respond)
    graph = build_planning_graph(runtime, checkpointer=InMemorySaver())
    value = {k: str(uuid4()) for k in ('user_id', 'project_id', 'session_id', 'run_id')}
    value.update(user_request='Compute total', model_selection=runtime.models.select().model_dump())
    config = {'configurable': {'thread_id': value['session_id']}}
    state = await graph.ainvoke(value, config, durability='sync')
    view = state['plan_views'][0]
    option = next(p for s in view['steps'] if s['step_id']=='calculate' for p in s['parameters'] if p['name']==case['option'])
    assert option['value'] == 1 and option['origin'] == 'tool_default' and option['editable']
    assert not executor.calls
    command = {'resume': {'action': 'approve_plan', 'plan_id': view['plan_id'], 'plan_revision': view['plan_revision'],
        'input_values': {'payload': '[3,5,7]'}, 'step_changes': [{'step_id': 'calculate', 'parameter': case['option'], 'value': 3}],
        'excluded_step_ids': ['optional'] if exclude else []}}
    boundary = state['__interrupt__'][0]
    identity = resume_identity(str(uuid4()), boundary.id, command)
    state = await graph.ainvoke(Command(resume={boundary.id: resume_envelope(identity, command)}), config, durability='sync')
    assert len(executor.calls) == 1
    import dtest.application.runs.persistence.graph as persistence
    async def persist(*args, **kwargs): return args[0]
    monkeypatch.setattr(persistence, 'persist_graph_state', persist)
    async def deliver(event):
        current = (await graph.aget_state(config)).values
        ctx = EventContext(namespace='test', session_id=value['session_id'], task_id=current['task_id'], execution_id=UUID(executor.id),
            command_id=uuid4(), event=ExecutorEvent.model_validate(event))
        await GraphInvocation(graph, model_validator=None).executor_resume(ctx)
        return (await graph.aget_state(config)).values
    state = await deliver(executor.events[0])
    observed = state['observations'][1]['summary']['items'][case['result']]
    assert (observed['items']['total'] if case['nested'] else observed) == 45
    if not exclude:
        assert state['execution_decisions']['continue_optional'] is True
        assert executor.calls[1][0].endswith('/operations')
        state = await deliver(executor.events[1])
    assert executor.calls[-1][0].endswith('/finalize')
    state = await deliver(executor.event('execution.completed', {'status': 'SUCCEEDED', 'error': None}))
    assert state['final_response']['status'] == 'analysis_completed' and state['report_status']=='ready'
    assert not (await graph.aget_state(config)).next
    assert state['approved_snapshot']['input_values']['payload']=='[3,5,7]'
    assert ('optional' in state['completed_steps']) is not exclude
    assert case['processor'] in state['approved_snapshot']['tool_sources']


@pytest.mark.parametrize('name', ['inventory', 'billing'])
def test_custom_alias_retains_registered_policy_including_readonly_references(tmp_path, name):
    catalog, case = assets(tmp_path, name)
    alias = 'custom.renamed'
    info = inherit_parameter_policy({'parameters': [case['reader_arg']], 'required_parameters': [case['reader_arg']]},
                                    catalog.metadata['tools'][case['reader']])
    metadata = deepcopy(catalog.metadata); metadata['tools'][alias] = info
    metadata['skills'][case['skill']]['tools'].append(alias)
    doc = definition(case); doc['steps'][0]['tool_id'] = alias
    assert not validate(doc, metadata)
    doc['steps'][0]['arguments'][case['reader_arg']] = {'source': 'literal', 'value': '[]'}
    assert any('binding source literal is not allowed' in e for e in validate(doc, metadata))
    assert info['parameter_bindings'] is not catalog.metadata['tools'][case['reader']]['parameter_bindings']


def test_binding_policy_revision_generator_preservation_and_changed_parameter_rejection(tmp_path):
    catalog, case = assets(tmp_path, 'inventory')
    path = tmp_path / 'tools/tool_registry.yaml'
    registry = build_registry(tmp_path / 'tools')
    assert registry['tools'][case['reader']]['parameter_bindings'][case['reader_arg']]['required']
    registry['tools'][case['reader']]['parameter_bindings'][case['reader_arg']]['required'] = False
    write_registry(registry, path)
    assert AssetCatalog(tmp_path).revision != catalog.revision
    source = tmp_path / 'tools/business_functions.py'
    source.write_text(source.read_text().replace('document', 'new_argument'))
    with pytest.raises(ValueError, match='Unknown binding policy'):
        build_registry(tmp_path / 'tools')
    with pytest.raises(ValueError, match='Unknown binding policy'):
        AssetCatalog(tmp_path)


def test_distinct_stable_ids_for_same_function_names_in_different_files(tmp_path):
    for name in ['one', 'two']:
        (tmp_path / f'{name}.py').write_text('def execute(value):\n    return value\n')
    with pytest.raises(ValueError, match='Duplicate Tool id'):
        build_registry(tmp_path)
    previous = {'tools': {name: {'source': f'{name}.py', 'function_name': 'execute'} for name in ['one', 'two']}}
    (tmp_path / 'tool_registry.yaml').write_text(yaml.safe_dump(previous))
    assert set(build_registry(tmp_path)['tools']) == {'one', 'two'}


@pytest.mark.parametrize('declaration', [
    {'x': {'allowed_sources': ['literal', 'literal']}},
    {'x': {'allowed_sources': ['literal'], 'input_kind': 'data_reference'}},
    {'x': {'allowed_sources': ['step_output'], 'unknown': True}},
    {'typo': {'allowed_sources': ['literal']}},
    {'x': {'allowed_sources': ['literal'], 'required': 'false'}},
])
def test_invalid_binding_policy_fails_before_serving(declaration):
    with pytest.raises(ValueError): parameter_bindings(declaration, ['x'])


def test_dataset_reference_contract_is_not_tied_to_loader_name_or_argument_name(tmp_path):
    catalog, case = assets(tmp_path, 'inventory')
    metadata = deepcopy(catalog.metadata)
    metadata['tools'][case['reader']]['parameter_bindings'][case['reader_arg']]['input_kind'] = 'data_reference'
    doc = definition(case, conditional=False); doc['inputs']['payload']['kind']='data_reference'
    review = new_review(doc, {'payload': 'source_a'}, metadata, POLICY)
    action = {'action': 'approve_plan', 'plan_id': review['plan_id'], 'plan_revision': 1}
    with pytest.raises(PlanReviewError, match='inaccessible'):
        patch_review(review, action, datasets={}, context=CONTEXT)
    datasets = {'source_a': {'scope': 'GLOBAL', 'runtime_path': '/runtime/custom/source.json'}}
    approved = patch_review(review, action, datasets=datasets, context=CONTEXT)
    frozen = freeze_approval(approved, catalog.sources, catalog.skill_sources, CONTEXT, catalog.revision, datasets)
    assert frozen['dataset_bindings']['payload']['runtime_path'] == '/runtime/custom/source.json'
    assert frozen['dataset_bindings']['payload']['dataset_id'] == 'source_a'


@pytest.mark.parametrize('name', ['inventory', 'billing'])
def test_revision_alias_and_carried_candidate_keep_origin_binding_policy(tmp_path, name):
    from dtest.agent_service.agents.analysis.planning.proposals import prepare_review, RevisionProposal, LocalFunction
    catalog, case = assets(tmp_path, name)
    runtime = PlanningRuntime(load_settings(config={'MODEL_PROVIDER': 'mock', 'AGENT_FREE_PLAN_ENABLED': True}, environ={}).agent,
                              catalog=catalog)
    doc = definition(case, conditional=False)
    doc['steps'][0]['tool_id'] = 'custom.adjusted_reader'
    code = catalog.sources[case['reader']]['code'].replace('import json', 'import json\n    marker = True')
    function = LocalFunction.from_code(step_id='decode', code=code, reason='Test contract-preserving implementation change',
                                      origin_tool_id=case['reader'])
    proposal = RevisionProposal(definition=doc, input_values={'payload': '[2,4,6]'}, functions=[function])
    state = {**CONTEXT, 'planning_revision_count': 1}
    review = prepare_review(proposal, runtime, state)
    assert review['catalog']['tools']['custom.adjusted_reader']['parameter_bindings'] == catalog.metadata['tools'][case['reader']]['parameter_bindings']
    carried = prepare_review(RevisionProposal(base_plan_id=review['plan_id']), runtime, {**state, 'reviews': [review]})
    assert carried['catalog']['tools']['custom.adjusted_reader']['parameter_bindings'] == review['catalog']['tools']['custom.adjusted_reader']['parameter_bindings']
    proposal.definition['steps'][0]['arguments'][case['reader_arg']] = {'source': 'literal', 'value': '[]'}
    with pytest.raises(PlanReviewError, match='binding source literal is not allowed'):
        prepare_review(proposal, runtime, state)


@pytest.mark.parametrize('name', ['inventory', 'billing'])
def test_repair_alias_cannot_erase_origin_parameter_policies(tmp_path, name):
    from dtest.agent_service.agents.analysis.execution.repair_policy import proposal_snapshot
    catalog, case = assets(tmp_path, name)
    review = new_review(definition(case, conditional=False), {'payload': '[2,4,6]'}, catalog.metadata, POLICY)
    approved = patch_review(review, {'action': 'approve_plan', 'plan_id': review['plan_id'], 'plan_revision': 1}, datasets={}, context=CONTEXT)
    frozen = freeze_approval(approved, catalog.sources, catalog.skill_sources, CONTEXT, catalog.revision)
    state = {'approved_snapshot': frozen, 'completed_steps': [], 'skipped_steps': [], 'execution_decisions': {},
        'observations': [{'step_id': 'decode', 'status': 'FAILED'}], 'failed_step_ids': ['decode'], 'repair_max_attempts': 2}
    code = catalog.sources[case['reader']]['code'].replace('import json', 'import json\n    marker = True')
    response = {'can_repair': True, 'summary': 'Correct the failed decoder', 'reason': 'Test interface-preserving correction',
        'evidence_steps': ['decode'], 'source_changes': [{'step_id': 'decode', 'code': code}]}
    candidate = proposal_snapshot(state, response, catalog, level_limit=4)
    assert candidate['snapshot']['steps'][0]['tool_id'].startswith('custom.repair_')
    response['argument_changes'] = [{'step_id': 'decode', 'arguments': {case['reader_arg']: {'source': 'literal', 'value': '[]'}}}]
    with pytest.raises(PlanReviewError, match='binding source literal is not allowed'):
        proposal_snapshot(state, response, catalog, level_limit=4)


def test_membership_only_change_changes_revision_and_unknown_tool_registration_fails(tmp_path):
    catalog, case = assets(tmp_path, 'inventory')
    index_path = tmp_path / 'skills/skill_index.yaml'
    index = yaml.safe_load(index_path.read_text())
    index['skills'][case['skill']]['tools'].pop()
    index_path.write_text(yaml.safe_dump(index))
    after = AssetCatalog(tmp_path)
    assert catalog.sources == after.sources and catalog.skill_sources == after.skill_sources
    assert catalog.revision != after.revision
    index['skills'][case['skill']]['tools'].append({'tool': 'missing_registration'})
    index_path.write_text(yaml.safe_dump(index))
    with pytest.raises(ValueError, match='unregistered Tools'):
        AssetCatalog(tmp_path)


def test_one_hundred_tools_eighteen_skills_no_fixed_names_or_vector_service(tmp_path):
    (tmp_path / 'tools').mkdir(); (tmp_path / 'skills').mkdir()
    source = '\n'.join(f'def operation_{i:03d}(amount=1):\n    """Operation tag_{i:03d}."""\n    return {{"answer_{i:03d}": amount}}\n' for i in range(100))
    (tmp_path / 'tools/bundle.py').write_text(source)
    write_registry(build_registry(tmp_path / 'tools'), tmp_path / 'tools/tool_registry.yaml')
    skills = {}
    for i in range(18):
        (tmp_path / f'skills/group_{i}.md').write_text(f'# Group {i}')
        skills[f'group_{i}'] = {'source': f'group_{i}.md', 'description': f'Group {i}',
                              'tools': [{'tool': f'operation_{j:03d}'} for j in range(i, 100, 18)]}
    (tmp_path / 'skills/skill_index.yaml').write_text(yaml.safe_dump({'skills': skills}))
    catalog = AssetCatalog(tmp_path)
    assert len(catalog.sources)==100 and len(catalog.public_skills())==18
    read, search = catalog.metadata_tools()
    result = search.invoke({'query': 'tag_099'})
    assert len(result)==1 and result[0]['tool_id']=='operation_099'
    assert set(read.invoke({'skill_id': 'group_9'})['tools'])=={f'operation_{j:03d}' for j in range(9, 100, 18)}


def test_symlinked_asset_root_uses_one_normalized_source_directory(tmp_path):
    catalog, _ = assets(tmp_path / 'real', 'inventory')
    alias = tmp_path / 'mounted'
    alias.symlink_to(tmp_path / 'real', target_is_directory=True)
    mounted = AssetCatalog(alias)
    assert mounted.revision == catalog.revision and mounted.sources == catalog.sources


def test_editable_parameter_cannot_contradict_reference_only_policy():
    with pytest.raises(ValueError, match='must allow literal'):
        parameter_bindings({'ref': {'allowed_sources': ['step_output']}}, ['ref'], {'ref': {'editable': True}})


def test_no_literal_default_materialized_for_reference_only_optional_parameter():
    from dtest.contracts.tool_parameters import materialize_defaults
    metadata = {'tools': {'arbitrary': {'parameter_controls': {'ref': {'has_default': True, 'default': None,
                'editable': False, 'value_schema': {'type': 'null'}}},
                'parameter_bindings': {'ref': {'allowed_sources': ['step_output'], 'required': False}}}}}
    doc = {'steps': [{'id': 'a', 'tool_id': 'arbitrary', 'arguments': {}}], 'decisions': []}
    updated, _ = materialize_defaults(doc, metadata)
    assert 'ref' not in updated['steps'][0]['arguments'] and doc == updated


def test_python_required_parameter_needs_value_even_when_workflow_and_policy_are_optional(tmp_path):
    catalog, case = assets(tmp_path, 'inventory')
    metadata = deepcopy(catalog.metadata)
    metadata['tools'][case['reader']]['parameter_bindings'][case['reader_arg']]['required'] = False
    doc = definition(case, conditional=False); doc['inputs']['payload']['required'] = False
    review = new_review(doc, {}, metadata, POLICY)
    with pytest.raises(PlanReviewError, match='Required Tool input is missing'):
        patch_review(review, {'action': 'approve_plan', 'plan_id': review['plan_id'], 'plan_revision': 1}, datasets={}, context=CONTEXT)


def test_optional_absent_data_reference_can_remain_absent_in_repair(tmp_path):
    from dtest.agent_service.agents.analysis.execution.repair_policy import proposal_snapshot
    catalog, case = assets(tmp_path, 'inventory')
    source = tmp_path / 'tools/business_functions.py'
    source.write_text(source.read_text().replace('document: str)', 'document: str=None)'))
    registry = yaml.safe_load((tmp_path/'tools/tool_registry.yaml').read_text())
    policy = registry['tools'][case['reader']]['parameter_bindings'][case['reader_arg']]
    policy.update(input_kind='data_reference', required=False)
    write_registry(registry, tmp_path/'tools/tool_registry.yaml')
    catalog = AssetCatalog(tmp_path)
    doc = definition(case, conditional=False)
    doc['inputs']['payload'].update(kind='data_reference', required=False)
    review = new_review(doc, {}, catalog.metadata, POLICY)
    approved = patch_review(review, {'action': 'approve_plan', 'plan_id': review['plan_id'], 'plan_revision': 1}, datasets={}, context=CONTEXT)
    frozen = freeze_approval(approved, catalog.sources, catalog.skill_sources, CONTEXT, catalog.revision)
    state = {'approved_snapshot': frozen, 'completed_steps': [], 'skipped_steps': [], 'execution_decisions': {},
        'observations': [{'step_id': 'decode', 'status': 'FAILED'}], 'failed_step_ids': ['decode'], 'repair_max_attempts': 2}
    code = catalog.sources[case['reader']]['code'].replace('import json', 'import json\n    document = document or "[]"')
    response = {'can_repair': True, 'summary': 'Handle the absent optional reference', 'reason': 'Fix default handling',
        'evidence_steps': ['decode'], 'source_changes': [{'step_id': 'decode', 'code': code}]}
    result = proposal_snapshot(state, response, catalog, level_limit=4)
    assert result['snapshot']['input_values']=={} and result['snapshot']['dataset_bindings']=={}
