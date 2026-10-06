"""Offline HITL-to-Executor contract prototype, not production service code.

Produces code-free public examples and internal submission examples. The local
probe executes only the repository's data_load/profile/statistics/outlier Tools
on a tiny temporary Parquet fixture. No model, HTTP API or Executor is called.
"""

from __future__ import annotations

import argparse
import ast
from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal
from uuid import UUID

from jsonschema_rs import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

import validate_workflow_draft as workflow_contract


ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / 'docs/design/plan-interaction-contract'


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ParameterChange(StrictModel):
    step_id: str
    parameter: str
    value: Any


class ExecutionOverrides(StrictModel):
    mode: Literal['SINGLE', 'MULTI'] | None = None
    repair_level: int | None = Field(default=None, ge=0, le=4)
    max_repair_attempts: int | None = Field(default=None, ge=0)


class PlanAction(StrictModel):
    action: Literal['edit_plan', 'approve_plan']
    plan_id: str
    plan_revision: int = Field(ge=1)
    input_values: dict[str, Any] = Field(default_factory=dict)
    step_changes: list[ParameterChange] = Field(default_factory=list)
    excluded_step_ids: list[str] = Field(default_factory=list)
    execution_overrides: ExecutionOverrides = Field(default_factory=ExecutionOverrides)


class ResumeCommand(StrictModel):
    resume: PlanAction


class ResumeRequest(StrictModel):
    run_id: UUID
    resume_token: UUID
    command: ResumeCommand


class InputView(StrictModel):
    name: str
    title: str
    description: str
    kind: Literal['parameter', 'data_reference']
    required: bool
    editable: bool
    value_schema: dict | bool
    origin: Literal['agent', 'workflow_default', 'user', 'unresolved']
    has_value: bool
    value: Any = None


class ParameterView(StrictModel):
    name: str
    kind: Literal['workflow_input', 'literal', 'step_reference', 'deferred', 'system_context']
    editable: bool
    value: Any = None
    value_schema: dict | bool | None = None
    input_name: str | None = None
    step_id: str | None = None
    selector: list[str | int] | None = None
    decision_id: str | None = None
    guidance: str | None = None
    context_key: str | None = None


class StepView(StrictModel):
    step_id: str
    skill_id: str
    tool_id: str
    function_name: str
    description: str
    depends_on: list[str]
    parameters: list[ParameterView]
    when: dict | None = None
    status: Literal['planned', 'excluded']


class DecisionView(StrictModel):
    decision_id: str
    evidence_steps: list[str]
    guidance: str
    value_schema: dict | bool
    status: Literal['deferred'] = 'deferred'


class OutputView(StrictModel):
    output_id: str
    kind: str
    description: str
    status: Literal['planned', 'excluded_by_user']


class PolicyView(StrictModel):
    mode: Literal['SINGLE', 'MULTI']
    repair_level: int
    max_repair_attempts: int
    review_mode: str
    review_interval_tools: int | None = None
    allowed_modes: list[Literal['SINGLE', 'MULTI']]
    repair_level_limit: int
    max_repair_attempts_limit: int


class SkillView(StrictModel):
    skill_id: str
    name: str
    description: str


class PlanView(StrictModel):
    plan_id: str
    plan_revision: int
    workflow_id: str
    definition_version: int
    name: str
    goal: str
    skills: list[SkillView]
    inputs: list[InputView]
    steps: list[StepView]
    decisions: list[DecisionView]
    execution: PolicyView
    outputs: list[OutputView]


class ReviewPayload(StrictModel):
    plans: list[PlanView] = Field(min_length=1)
    notices: list[str] = Field(default_factory=list)


class InteractionData(StrictModel):
    interaction_id: UUID
    revision: int = Field(ge=1)
    kind: Literal['plan_review']
    status: Literal['open']
    resume_token: UUID
    summary: str
    payload: ReviewPayload


class InteractionEvent(StrictModel):
    schema_version: Literal[1] = 1
    type: Literal['interaction.opened', 'interaction.updated']
    sequence: int = Field(ge=1)
    session_id: UUID
    run_id: UUID
    occurred_at: str
    data: InteractionData


class ResolutionPayload(StrictModel):
    approved_plan: PlanView
    notices: list[str] = Field(default_factory=list)


class ResolutionData(StrictModel):
    interaction_id: UUID
    revision: int = Field(ge=1)
    kind: Literal['plan_review']
    status: Literal['resolved']
    resolution: Literal['approved']
    payload: ResolutionPayload


class InteractionResolvedEvent(StrictModel):
    schema_version: Literal[1] = 1
    type: Literal['interaction.resolved']
    sequence: int = Field(ge=1)
    session_id: UUID
    run_id: UUID
    occurred_at: str
    data: ResolutionData


class ContractError(ValueError):
    pass


def ensure(condition, reason):
    if not condition:
        raise ContractError(reason)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def value_valid(schema, value):
    return Draft202012Validator(schema).is_valid(value)


def make_state(document, initial_values, context):
    values, origins = {}, {}
    for name, field in document['inputs'].items():
        if 'default' in field:
            values[name], origins[name] = field['default'], 'workflow_default'
    for name, value in initial_values.items():
        ensure(name in document['inputs'], 'unknown proposed input')
        values[name], origins[name] = value, 'agent'
    decision_map = {item['id']: item for item in document['decisions']}
    editable = {step['id']: {name: decision_map[binding['decision_id']]['output_schema']
                for name, binding in step['arguments'].items() if binding['source'] == 'agent_decision'}
                for step in document['steps']}
    for step in document['steps']:
        for name, control in step.get('parameter_controls', {}).items():
            if not control['editable']:
                editable[step['id']].pop(name, None)
            elif step['arguments'][name]['source'] == 'agent_decision':
                editable[step['id']][name] = {'allOf': [editable[step['id']][name], control['value_schema']]}
            else:
                ensure(step['arguments'][name]['source'] == 'literal', 'reference/system parameter cannot be declared editable')
                editable[step['id']][name] = control['value_schema']
    return {'document': deepcopy(document), 'input_values': values, 'input_origins': origins,
            'editable_parameters': editable,
            'plan_id': 'plan-quality-review', 'plan_revision': 1,
            'event_sequence': 58,
            'interaction_revision': 1, 'resume_token': '11111111-1111-4111-8111-111111111111',
            'interaction_id': '22222222-2222-4222-8222-222222222222',
            'context': deepcopy(context), 'excluded_step_ids': [], 'user_actions': [], 'notices': [], 'consumed': False}


def needed_decisions(state):
    used = {binding['decision_id'] for step in state['document']['steps']
            if step['id'] not in state['excluded_step_ids']
            for binding in workflow_contract.bindings([step['arguments'], step.get('when')])
            if binding['source'] == 'agent_decision'}
    return [item for item in state['document']['decisions'] if item['id'] in used]


def project(state, catalog, *, updated=False):
    document = state['document']
    decisions = {item['id']: item for item in document['decisions']}
    fields = []
    for name, item in document['inputs'].items():
        fields.append(InputView(name=name, title=item['title'], description=item['description'],
            kind=item['kind'], required=item['required'], editable=item['editable'], value_schema=item['value_schema'],
            has_value=name in state['input_values'], value=state['input_values'].get(name),
            origin=state['input_origins'].get(name, 'unresolved')))
    step_views = []
    for step in document['steps']:
        parameters = []
        for name, binding in step['arguments'].items():
            source = binding['source']
            if source == 'workflow_input':
                parameters.append(ParameterView(name=name, kind='workflow_input', editable=False,
                                                input_name=binding['name']))
            elif source == 'step_output':
                parameters.append(ParameterView(name=name, kind='step_reference', editable=False,
                                                step_id=binding['step_id'], selector=binding['selector']))
            elif source == 'agent_decision':
                decision = decisions[binding['decision_id']]
                schema = state['editable_parameters'].get(step['id'], {}).get(name)
                parameters.append(ParameterView(name=name, kind='deferred', editable=schema is not None,
                    decision_id=decision['id'], guidance=decision['instruction'], value_schema=schema if schema is not None else decision['output_schema']))
            elif source == 'system_context':
                parameters.append(ParameterView(name=name, kind='system_context', editable=False, context_key=binding['key']))
            else:
                schema = state['editable_parameters'].get(step['id'], {}).get(name)
                parameters.append(ParameterView(name=name, kind='literal', editable=schema is not None,
                                                value=binding['value'], value_schema=schema))
        step_views.append(StepView(step_id=step['id'], skill_id=step['skill_id'], tool_id=step['tool_id'],
            function_name=catalog['tools'][step['tool_id']]['function_name'], description=step['description'],
            depends_on=step['depends_on'], parameters=parameters, when=step.get('when'),
            status='excluded' if step['id'] in state['excluded_step_ids'] else 'planned'))
    outputs = [OutputView(output_id=item['id'], kind=item['kind'], description=item['description'],
                         status='excluded_by_user' if item['source'].get('step_id') in state['excluded_step_ids'] else 'planned')
               for item in document['expected_outputs']]
    plan = PlanView(plan_id=state['plan_id'], plan_revision=state['plan_revision'],
        workflow_id=document['workflow_id'], definition_version=document['definition_version'],
        name=document['name'], goal=document['goal'], inputs=fields, steps=step_views,
        skills=[SkillView(skill_id=id, name=id, description=catalog['skills'][id].get('description', id))
                for id in dict.fromkeys(step['skill_id'] for step in document['steps'])],
        decisions=[DecisionView(decision_id=item['id'], evidence_steps=item['after_steps'],
                               guidance=item['instruction'], value_schema=item['output_schema']) for item in needed_decisions(state)],
        execution=PolicyView(**document['execution'],
            allowed_modes=['MULTI'] if document['decisions'] or any('when' in item for item in document['steps']) else ['SINGLE', 'MULTI'],
            repair_level_limit=state['context']['max_repair_level'], max_repair_attempts_limit=state['context']['max_repair_attempts']), outputs=outputs)
    event = InteractionEvent(type='interaction.updated' if updated else 'interaction.opened',
        sequence=state['event_sequence'], session_id=state['context']['session_id'],
        run_id=state['context']['run_id'], occurred_at='2026-09-30T00:00:00Z',
        data=InteractionData(interaction_id=state['interaction_id'], revision=state['interaction_revision'],
            kind='plan_review', status='open', resume_token=state['resume_token'],
            summary='계획과 입력값을 확인하고 승인해 주세요.', payload=ReviewPayload(plans=[plan], notices=state['notices'])))
    # Missing/deferred values are omitted; explicit literal null retains its meaning.
    result = event.model_dump(mode='json', exclude_none=True)
    for item in result['data']['payload']['plans'][0]['inputs']:
        if item['has_value'] and state['input_values'][item['name']] is None:
            item['value'] = None
    for step in result['data']['payload']['plans'][0]['steps']:
        for parameter in step['parameters']:
            if parameter['kind'] == 'literal' and next(item for item in document['steps'] if item['id'] == step['step_id'])['arguments'][parameter['name']]['value'] is None:
                parameter['value'] = None
    return result


def check_scope(reference, context):
    ensure(reference['owner_user_id'] == context['user_id'], 'dataset belongs to another user')
    if reference['scope'] in {'PROJECT', 'SESSION'}:
        ensure(reference['project_id'] == context['project_id'], 'dataset belongs to another project')
    if reference['scope'] == 'SESSION':
        ensure(reference['session_id'] == context['session_id'], 'dataset belongs to another session')
    ensure(reference['scope'] in {'USER', 'PROJECT', 'SESSION'}, 'unknown dataset scope')


def apply_action(state, raw_request, catalog, repository_root):
    request = ResumeRequest.model_validate(raw_request)
    ensure(not state['consumed'], 'interaction already consumed')
    ensure(str(request.run_id) == state['context']['run_id'], 'run mismatch')
    ensure(str(request.resume_token) == state['resume_token'], 'stale resume token')
    action = request.command.resume
    ensure(action.plan_id == state['plan_id'], 'plan mismatch')
    ensure(action.plan_revision == state['plan_revision'], 'stale plan revision')
    result = deepcopy(state)
    document = result['document']
    for name, value in action.input_values.items():
        ensure(name in document['inputs'], 'unknown input field')
        field = document['inputs'][name]
        ensure(field['editable'], 'input is read only')
        ensure(value_valid(field['value_schema'], value), 'invalid input value')
        result['input_values'][name] = value
        result['input_origins'][name] = 'user'
    ensure(len(action.excluded_step_ids) == len(set(action.excluded_step_ids)), 'duplicate excluded step')
    step_map = {step['id']: step for step in document['steps']}
    excluded = set(action.excluded_step_ids) if 'excluded_step_ids' in action.model_fields_set else set(state['excluded_step_ids'])
    ensure(excluded <= step_map.keys(), 'unknown excluded step')
    ensure(len(excluded) < len(step_map), 'cannot exclude all execution steps')
    # Omission preserves current exclusions; a supplied list is a full replacement.
    result['excluded_step_ids'] = sorted(excluded)
    for id, step in step_map.items():
        if id not in excluded:
            ensure(not (set(step['depends_on']) & excluded), f'exclusion breaks dependencies of {id}; replan required')
    decisions = {item['id']: item for item in document['decisions']}
    seen = set()
    for change in action.step_changes:
        key = (change.step_id, change.parameter)
        ensure(key not in seen, 'duplicate parameter patch')
        seen.add(key)
        ensure(change.step_id in step_map and change.step_id not in excluded, 'unknown or excluded patched step')
        arguments = step_map[change.step_id]['arguments']
        ensure(change.parameter in arguments, 'parameter not exposed in plan')
        schema = result['editable_parameters'].get(change.step_id, {}).get(change.parameter)
        ensure(schema is not None, 'reference/system/literal parameter is not editable in this prototype')
        ensure(value_valid(schema, change.value), 'parameter override outside approved schema')
        arguments[change.parameter] = {'source': 'literal', 'value': change.value}
    overrides = action.execution_overrides.model_dump(exclude_none=True)
    ensure(overrides.get('max_repair_attempts', 0) <= state['context']['max_repair_attempts'], 'repair attempts exceed service limit')
    document['execution'].update(overrides)
    ensure(document['execution']['repair_level'] <= state['context']['max_repair_level'], 'repair level exceeds service limit')
    ensure(document['execution']['max_repair_attempts'] <= state['context']['max_repair_attempts'], 'repair attempts exceed service limit')
    errors = workflow_contract.validate(document, catalog, repository_root=repository_root)
    ensure(not errors, f'invalid edited definition: {errors}')
    for decision in needed_decisions(result):
        ensure(not (set(decision['after_steps']) & excluded), 'exclusion removes required decision evidence')
    changed = result['document'] != state['document'] or result['input_values'] != state['input_values'] or result['excluded_step_ids'] != state['excluded_step_ids']
    if changed:
        result['plan_revision'] += 1
    result['notices'] = [f"제외된 단계: {', '.join(sorted(excluded))}. 해당 산출물은 생성하지 않습니다."] if excluded else []
    result['user_actions'].append(action.model_dump(mode='json', exclude_unset=True))
    if action.action == 'edit_plan':
        result['interaction_revision'] += 1
        result['event_sequence'] += 1
        # Production must use a fresh unpredictable token; this is a deterministic fixture.
        result['resume_token'] = '33333333-3333-4333-8333-333333333333'
        return result, None
    for name, field in document['inputs'].items():
        ensure(not field['required'] or name in result['input_values'], f'required input missing: {name}')
        if name in result['input_values']:
            ensure(value_valid(field['value_schema'], result['input_values'][name]), 'invalid final input')
            if field['kind'] == 'data_reference':
                ref = result['context']['dataset_refs'].get(result['input_values'][name])
                ensure(ref is not None, 'unknown dataset reference')
                check_scope(ref, result['context'])
    result['consumed'] = True
    result['event_sequence'] += 1
    snapshot = freeze(result, catalog, repository_root)
    return result, snapshot


def resolved_event(state, catalog):
    ensure(state['consumed'], 'interaction is not approved')
    view = project(state, catalog)
    event = InteractionResolvedEvent(type='interaction.resolved', sequence=state['event_sequence'], session_id=view['session_id'],
        run_id=view['run_id'], occurred_at=view['occurred_at'],
        data=ResolutionData(interaction_id=state['interaction_id'], revision=state['interaction_revision'],
            kind='plan_review', status='resolved', resolution='approved',
            payload=ResolutionPayload(approved_plan=PlanView.model_validate(view['data']['payload']['plans'][0]), notices=state['notices'])))
    result = event.model_dump(mode='json', exclude_none=True)
    result['data']['payload']['approved_plan'] = view['data']['payload']['plans'][0]
    return result


def strip_tool_function(file, function_name):
    original = file.read_text(encoding='utf-8')
    module = ast.parse(original)
    function = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == function_name)
    ensure(not function.decorator_list, 'decorated Tools need a separate extraction contract')
    raw = ast.get_source_segment(original, function)
    local = ast.parse(raw).body[0]
    expected = deepcopy(local)
    if expected.body and isinstance(expected.body[0], ast.Expr) and isinstance(expected.body[0].value, ast.Constant) and isinstance(expected.body[0].value.value, str):
        doc = local.body[0]
        expected.body.pop(0)
        lines = raw.splitlines(keepends=True)
        # Preserve every other source byte, including comments and imports.
        first = lines[doc.lineno - 1]
        last = lines[doc.end_lineno - 1]
        ensure(not first[:doc.col_offset].strip() and not last[doc.end_col_offset:].strip(), 'inline docstring extraction unsupported')
        lines[doc.lineno - 1:doc.end_lineno] = ['\n'] * (doc.end_lineno - doc.lineno + 1)
        raw = ''.join(lines)
    ensure(ast.dump(ast.parse(raw).body[0], include_attributes=False) == ast.dump(expected, include_attributes=False), 'Tool body changed beyond docstring removal')
    return {'function_name': function_name, 'code': raw + '\n',
            'source_sha256': hashlib.sha256(ast.get_source_segment(original, function).encode()).hexdigest(),
            'code_sha256': hashlib.sha256((raw + '\n').encode()).hexdigest()}


def freeze(state, catalog, repository_root):
    active = [step for step in state['document']['steps'] if step['id'] not in state['excluded_step_ids']]
    sources = {}
    for step in active:
        id = step['tool_id']
        if id not in sources:
            item = catalog['tools'][id]
            sources[id] = strip_tool_function(repository_root / item['source_file'], item['function_name'])
    import yaml
    assets = repository_root / 'src/dtest.agent_service/agents/analysis/workflow'
    skill_index = yaml.safe_load((assets / 'skills/skill_index.yaml').read_text())['skills']
    skill_sources = {}
    for id in {step['skill_id'] for step in active}:
        markdown = (assets / 'skills' / skill_index[id]['source']).read_text()
        skill_sources[id] = {'markdown': markdown, 'sha256': hashlib.sha256(markdown.encode()).hexdigest()}
    snapshot = {'schema_version': 'plan-snapshot-1-draft', 'run_id': state['context']['run_id'],
                'plan_id': state['plan_id'], 'plan_revision': state['plan_revision'],
                'workflow_id': state['document']['workflow_id'], 'definition_version': state['document']['definition_version'],
                'steps': deepcopy(active), 'decisions': deepcopy(needed_decisions(state)),
                'input_values': deepcopy(state['input_values']),
                'input_kinds': {name: item['kind'] for name, item in state['document']['inputs'].items()},
                'execution': deepcopy(state['document']['execution']), 'skill_sources': skill_sources,
                'excluded_step_ids': state['excluded_step_ids'], 'user_actions': deepcopy(state['user_actions']), 'tool_sources': sources,
                'context': deepcopy(state['context'])}
    snapshot['approval_sha256'] = digest(snapshot)
    return snapshot


OBSERVATION_HELPER = '''\ndef _dtest_observe(value, depth=0):
    if isinstance(value, float) and not __import__("math").isfinite(value):
        return {"type": "float", "value": None, "non_finite": str(value)}
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= 240 else {"type": "str", "text": value[:240], "truncated": True}
    if depth >= 3:
        return {"type": type(value).__name__, "truncated": True}
    if type(value).__name__ == "DataFrame":
        return {"type": "DataFrame", "shape": list(value.shape),
                "columns": [str(item) for item in list(value.columns[:10])],
                "preview": _dtest_observe(value.iloc[:5, :10].to_dict(orient="records"), depth + 1),
                "truncated": len(value) > 5 or len(value.columns) > 10}
    if isinstance(value, dict):
        selected = list(__import__("itertools").islice(value.items(), 12))
        return {"type": "dict", "items": {str(key)[:120]: _dtest_observe(item, depth + 1) for key, item in selected},
                "truncated": len(value) > len(selected)}
    if isinstance(value, (list, tuple)):
        return {"type": type(value).__name__, "items": [_dtest_observe(item, depth + 1) for item in value[:5]],
                "truncated": len(value) > 5}
    return {"type": type(value).__name__}
'''


def variable(step_id):
    return '_dtest_result_' + hashlib.sha256(step_id.encode()).hexdigest()[:12]


def resolve(binding, snapshot, decisions):
    source = binding['source']
    if source == 'literal':
        return binding['value']
    if source == 'workflow_input':
        name = binding['name']
        value = snapshot['input_values'][name]
        reference = snapshot['context']['dataset_refs'].get(value) if snapshot['input_kinds'][name] == 'data_reference' else None
        return reference['resolved_path'] if reference else value
    if source == 'agent_decision':
        ensure(binding['decision_id'] in decisions, 'deferred decision not ready')
        return decisions[binding['decision_id']]
    if source == 'system_context':
        return snapshot['context'][binding['key']]
    raise ContractError('step objects are references, not serialized values')


def condition_value(condition, snapshot, decisions):
    if 'all' in condition:
        values = [condition_value(item, snapshot, decisions) for item in condition['all']]
        return all(values)
    if 'any' in condition:
        values = [condition_value(item, snapshot, decisions) for item in condition['any']]
        return any(values)
    if 'not' in condition:
        return not condition_value(condition['not'], snapshot, decisions)
    left, right = (resolve(condition[key], snapshot, decisions) for key in ['left', 'right'])
    op = condition['op']
    if op in {'eq', 'ne'}:
        ensure(type(left) is type(right), 'condition types mismatch')
        equal = left == right
        return equal if op == 'eq' else not equal
    raise ContractError('prototype executes only eq/ne conditions; other draft operators are not implemented')


def ready_batch(snapshot, completed, decisions):
    ensure(snapshot['approval_sha256'] == digest({key: value for key, value in snapshot.items() if key != 'approval_sha256'}), 'approved snapshot changed')
    decision_map = {item['id']: item for item in snapshot['decisions']}
    for id, value in decisions.items():
        ensure(id in decision_map, 'unapproved or overridden decision value')
        ensure(set(decision_map[id]['after_steps']) <= set(completed), 'decision evidence not complete')
        ensure(value_valid(decision_map[id]['output_schema'], value), 'invalid decision output')
    available = set(completed)
    batch, skipped = [], []
    for step in snapshot['steps']:
        if step['id'] in completed:
            continue
        ensure(set(step['depends_on']) <= available, 'dependent step is not ready')
        guard_decisions = {binding['decision_id'] for binding in workflow_contract.bindings(step.get('when')) if binding['source'] == 'agent_decision'}
        if not guard_decisions <= decisions.keys():
            break  # Stop before the first result-based judgment boundary.
        if step.get('when') and not condition_value(step['when'], snapshot, decisions):
            skipped.append(step['id'])
            continue
        argument_decisions = {binding['decision_id'] for binding in workflow_contract.bindings(step['arguments']) if binding['source'] == 'agent_decision'}
        if not argument_decisions <= decisions.keys():
            break
        batch.append(step)
        available.add(step['id'])
    return batch, skipped


def compile_steps(snapshot, steps, decisions, start_sequence):
    result = []
    for sequence, step in enumerate(steps, start_sequence):
        source = snapshot['tool_sources'][step['tool_id']]
        ensure(hashlib.sha256(source['code'].encode()).hexdigest() == source['code_sha256'], 'frozen source changed')
        arguments = []
        lineage = {}
        for name, binding in step['arguments'].items():
            if binding['source'] == 'step_output':
                expression = variable(binding['step_id']) + ''.join(f'[{part!r}]' for part in binding['selector'])
                lineage[name] = {'step_id': binding['step_id'], 'selector': binding['selector']}
            else:
                value = resolve(binding, snapshot, decisions)
                canonical(value)  # Reject NaN and non-JSON objects before literal generation.
                expression = repr(value)
                # Lineage records user-facing references, not raw resolved storage paths.
                lineage[name] = snapshot['input_values'][binding['name']] if binding['source'] == 'workflow_input' else value
            arguments.append(f'{name}={expression}')
        call = f"{variable(step['id'])} = {source['function_name']}({', '.join(arguments)})\n"
        observe = f"print('DTEST_OBSERVATION ' + __import__('json').dumps({{'step_id': {step['id']!r}, 'summary': _dtest_observe({variable(step['id'])})}}, ensure_ascii=False, default=str))\n"
        code = source['code'] + OBSERVATION_HELPER + '\n' + call + observe
        ast.parse(code)
        result.append({'sequence': sequence, 'payload': {'type': 'PYTHON_EXECUTE', 'source': {'type': 'INLINE', 'content': code}},
                       'lineage': {'skill_name': step['skill_id'], 'tool_name': step['tool_id'], 'input_parameters': lineage}})
    return result


def submission(snapshot):
    steps, skipped = ready_batch(snapshot, [], {})
    ensure(steps and not skipped, 'first operation needs runnable steps')
    context = snapshot['context']
    ensure(snapshot['execution']['mode'] == 'MULTI', 'prototype example implements MULTI only')
    return {'idempotency_key': f"{snapshot['run_id']}:plan:{snapshot['plan_revision']}:operation:1",
            'lifecycle': {'operation_mode': 'MULTI', 'operation_wait_timeout_seconds': context['operation_wait_timeout_seconds']},
            'trigger': {'type': 'INTERACTIVE', 'actor': {'type': 'AGENT', 'id': 'analysis'}},
            'runtime': {'type': 'JUPYTER', 'profile': context['kernel_profile']},
            'context': {key: context[key] for key in ['user_id', 'task_id', 'project_id', 'session_id']} | {'workflow_id': snapshot['workflow_id']},
            'operation': {'spec': {'schema_version': '1.0', 'steps': compile_steps(snapshot, steps, {}, 0)},
                          'metadata': {'plan_id': snapshot['plan_id'], 'plan_revision': snapshot['plan_revision'],
                                       'logical_steps': [{'sequence': sequence, 'plan_step_id': step['id']} for sequence, step in enumerate(steps)]}},
            'metadata': {'public_run_id': snapshot['run_id'], 'approval_sha256': snapshot['approval_sha256']}}


def next_operation(snapshot, completed, decisions, *, expected_version, next_sequence):
    steps, skipped = ready_batch(snapshot, completed, decisions)
    if not steps:
        ensure(set(completed) | set(skipped) == {step['id'] for step in snapshot['steps']}, 'unresolved decisions remain; do not finalize')
        return None, skipped
    return {'idempotency_key': f"{snapshot['run_id']}:plan:{snapshot['plan_revision']}:operation:2",
            'expected_version': expected_version, 'spec': {'schema_version': '1.0', 'steps': compile_steps(snapshot, steps, decisions, next_sequence)},
            'actor': {'type': 'AGENT', 'id': 'analysis'},
            'metadata': {'public_run_id': snapshot['run_id'], 'plan_revision': snapshot['plan_revision'],
                         'logical_steps': [{'sequence': sequence, 'plan_step_id': step['id']} for sequence, step in enumerate(steps, next_sequence)]}}, skipped


def context_fixture():
    return {'run_id': '44444444-4444-4444-8444-444444444444',
            'session_id': '55555555-5555-4555-8555-555555555555',
            'project_id': '66666666-6666-4666-8666-666666666666',
            'task_id': '77777777-7777-4777-8777-777777777777',
            'user_id': 'user-demo', 'kernel_profile': 'default',
            'operation_wait_timeout_seconds': 600, 'max_repair_attempts': 3, 'max_repair_level': 4,
            'dataset_refs': {'dataset-demo': {'scope': 'PROJECT', 'owner_user_id': 'user-demo',
                'project_id': '66666666-6666-4666-8666-666666666666',
                'resolved_path': '/executor-data/demo/quality.parquet'}}}


def request_fixture(state, *, action='approve_plan', **changes):
    return {'run_id': state['context']['run_id'], 'resume_token': state['resume_token'],
            'command': {'resume': {'action': action, 'plan_id': state['plan_id'], 'plan_revision': state['plan_revision'], **changes}}}


def generate(repository_root, destination):
    document = workflow_contract.read(workflow_contract.CONTRACT / 'examples/quality-review.repository.json')
    catalog = workflow_contract.read(workflow_contract.CONTRACT / 'examples/repository-catalog.json')
    import yaml
    skill_index = yaml.safe_load((repository_root / 'src/dtest.agent_service/agents/analysis/workflow/skills/skill_index.yaml').read_text())['skills']
    for id in catalog['skills']:
        catalog['skills'][id]['description'] = skill_index[id]['description']
    context = context_fixture()
    state = make_state(document, {'dataset': 'dataset-demo'}, context)
    public = project(state, catalog)
    request = request_fixture(state, step_changes=[{'step_id': 'outliers', 'parameter': 'method', 'value': 'iqr'}])
    approved, snapshot = apply_action(state, request, catalog, repository_root)
    initial = submission(snapshot)
    operation, skipped = next_operation(snapshot, ['load', 'profile', 'statistics'], {'inspect_outliers': True}, expected_version=4, next_sequence=3)
    finalize = {'idempotency_key': f"{snapshot['run_id']}:finalize", 'expected_version': 7, 'actor': {'type': 'AGENT', 'id': 'analysis'}}
    artifacts = {
        'resume-request.schema.json': ResumeRequest.model_json_schema(),
        'interaction-event.schema.json': TypeAdapter(InteractionEvent | InteractionResolvedEvent).json_schema(),
        'examples/public/interaction.opened.json': public,
        'examples/public/approve-plan.request.json': request,
        'examples/public/interaction.resolved.json': resolved_event(approved, catalog),
        'examples/internal/approved-plan.snapshot.json': snapshot,
        'examples/internal/executor-submit.request.json': initial,
        'examples/internal/executor-operation.request.json': operation,
        'examples/internal/executor-finalize.request.json': finalize,
    }
    edited, unused = apply_action(state, request_fixture(state, action='edit_plan', excluded_step_ids=['outliers']), catalog, repository_root)
    artifacts['examples/public/interaction.updated.json'] = project(edited, catalog, updated=True)
    artifacts['examples/public/edit-plan.request.json'] = request_fixture(state, action='edit_plan', excluded_step_ids=['outliers'])
    for name, value in artifacts.items():
        file = destination / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    return document, catalog, state, approved, snapshot, initial, operation


def probe(repository_root, destination):
    document, catalog, state, approved, snapshot, initial, operation = generate(repository_root, destination)
    checks = []

    def check(name, operation, expected_error=False):
        try:
            detail = operation()
            passed = not expected_error
        except (ContractError, ValidationError) as error:
            detail, passed = str(error), expected_error
        checks.append({'name': name, 'passed': passed, 'detail': detail})

    check('public_schema_and_no_source', lambda: public_probe(state, catalog))
    check('initial_operation_stops_before_judgment', lambda: ensure([step['lineage']['tool_name'] for step in initial['operation']['spec']['steps']] == ['data_load', 'profile_data', 'compute_statistics'], 'wrong first batch'))
    check('next_sequence_continues', lambda: ensure(operation['spec']['steps'][0]['sequence'] == 3, 'sequence reset'))
    check('approved_parameter_override', lambda: ensure(operation['spec']['steps'][0]['lineage']['input_parameters']['method'] == 'iqr', 'override lost'))
    check('docstring_only_source_change', lambda: ensure(all(ast.get_docstring(ast.parse(item['code']).body[0]) is None for item in snapshot['tool_sources'].values()), 'docstring remains'))
    check('all_generated_code_parses', lambda: [ast.parse(step['payload']['source']['content']) and True for body in [initial['operation'], operation] for step in body['spec']['steps']])
    false_operation, skipped = next_operation(snapshot, ['load', 'profile', 'statistics'], {'inspect_outliers': False}, expected_version=4, next_sequence=3)
    check('false_branch_produces_no_empty_operation', lambda: ensure(false_operation is None and skipped == ['outliers'], 'false branch submitted'))
    check('missing_required_input_is_blank', lambda: ensure(project(make_state(document, {}, state['context']), catalog)['data']['payload']['plans'][0]['inputs'][0]['has_value'] is False, 'missing input got default'))
    check('resolved_event_contains_approved_revision', lambda: ensure(resolved_event(approved, catalog)['data']['payload']['approved_plan']['plan_revision'] == 2, 'approved revision missing'))

    def attempt(**patch):
        return apply_action(state, request_fixture(state, **patch), catalog, repository_root)

    check('invalid_method_override_rejected', lambda: attempt(step_changes=[{'step_id': 'outliers', 'parameter': 'method', 'value': 'unknown'}]), True)
    check('object_reference_edit_rejected', lambda: attempt(step_changes=[{'step_id': 'profile', 'parameter': 'data', 'value': 'arbitrary'}]), True)
    check('broken_dependency_exclusion_rejected', lambda: attempt(excluded_step_ids=['load']), True)
    check('decision_evidence_exclusion_rejected', lambda: attempt(excluded_step_ids=['statistics']), True)
    check('single_with_deferred_decisions_rejected', lambda: attempt(execution_overrides={'mode': 'SINGLE'}), True)
    check('repair_limit_rejected', lambda: attempt(execution_overrides={'max_repair_attempts': 100}), True)
    bad = request_fixture(state)
    bad['resume_token'] = '99999999-9999-4999-8999-999999999999'
    check('stale_token_rejected', lambda: apply_action(state, bad, catalog, repository_root), True)
    bad = request_fixture(state)
    bad['command']['resume']['plan_revision'] = 99
    check('stale_revision_rejected', lambda: apply_action(state, bad, catalog, repository_root), True)
    bad = request_fixture(state)
    bad['command']['resume']['code'] = "print('unregistered')"
    check('client_code_rejected', lambda: apply_action(state, bad, catalog, repository_root), True)
    bad = request_fixture(state)
    bad['user_id'] = 'another-user'
    check('client_identity_override_rejected', lambda: apply_action(state, bad, catalog, repository_root), True)
    check('consumed_interaction_rejected', lambda: apply_action(approved, request_fixture(approved), catalog, repository_root), True)
    check('decision_without_evidence_rejected', lambda: next_operation(snapshot, ['load'], {'inspect_outliers': True}, expected_version=4, next_sequence=1), True)
    check('unresolved_decision_cannot_finalize', lambda: next_operation(snapshot, ['load', 'profile', 'statistics'], {}, expected_version=4, next_sequence=3), True)
    wrong_scope = deepcopy(state)
    wrong_scope['context']['dataset_refs']['dataset-demo']['owner_user_id'] = 'another-user'
    check('dataset_scope_rejected', lambda: apply_action(wrong_scope, request_fixture(wrong_scope), catalog, repository_root), True)
    tampered = deepcopy(snapshot)
    tampered['steps'][0]['arguments']['parquet_path'] = {'source': 'literal', 'value': 'changed'}
    check('approved_snapshot_tampering_rejected', lambda: submission(tampered), True)
    check('leaf_exclusion_updates_outputs', lambda: exclusion_probe(state, catalog, repository_root))
    check('false_condition_does_not_require_unused_parameter_decision', lambda: false_condition_probe(state, catalog, repository_root))
    check('parameter_remains_editable_after_edit', lambda: repeated_edit_probe(state, catalog, repository_root))
    check('excluded_steps_preserved_when_omitted', lambda: preserve_exclusion_probe(state, catalog, repository_root))
    check('explicit_null_is_distinct_from_missing', lambda: explicit_null_probe(document, catalog, repository_root))
    check('declared_literal_parameter_is_editable', lambda: literal_control_probe(document, catalog, repository_root))
    check('actual_tool_execution_on_small_fixture', lambda: local_execution_probe(document, catalog, repository_root))
    return {'scope': 'offline contracts, generated code and local tiny-data Tool execution; no LLM/HTTP/DB/Executor service',
            'passed': all(item['passed'] for item in checks), 'checks': checks}


def public_probe(state, catalog):
    event = project(state, catalog)
    InteractionEvent.model_validate(event)
    text = canonical(event)
    ensure('def data_load(' not in text and 'def detect_outliers(' not in text, 'source leaked to frontend')
    ensure('resolved_path' not in text and '/executor-data/' not in text, 'storage path leaked')
    ensure(all('code' not in step for step in event['data']['payload']['plans'][0]['steps']), 'code field leaked')
    return {'source_hidden': True, 'storage_paths_hidden': True}


def exclusion_probe(state, catalog, repository_root):
    edited, snapshot = apply_action(state, request_fixture(state, action='edit_plan', excluded_step_ids=['outliers']), catalog, repository_root)
    ensure(snapshot is None and not edited['consumed'], 'edit executed')
    event = project(edited, catalog, updated=True)
    ensure(event['type'] == 'interaction.updated', 'wrong event type')
    plan = event['data']['payload']['plans'][0]
    ensure(plan['plan_revision'] == 2 and not plan['decisions'], 'revision/unused decisions not updated')
    ensure(next(item for item in plan['outputs'] if item['output_id'] == 'outlier_result')['status'] == 'excluded_by_user', 'excluded output hidden silently')
    ensure(edited['resume_token'] != state['resume_token'], 'token not rotated')
    _, frozen = apply_action(edited, request_fixture(edited, excluded_step_ids=['outliers']), catalog, repository_root)
    ensure([step['id'] for step in frozen['steps']] == ['load', 'profile', 'statistics'], 'excluded Tool still submitted')
    return {'revision': 2, 'excluded_output_visible': True, 'no_automatic_execution': True}


def false_condition_probe(state, catalog, repository_root):
    _, snapshot = apply_action(state, request_fixture(state), catalog, repository_root)
    operation, skipped = next_operation(snapshot, ['load', 'profile', 'statistics'], {'inspect_outliers': False}, expected_version=4, next_sequence=3)
    ensure(operation is None and skipped == ['outliers'], 'unused method decision was required')
    return {'method_not_requested': True, 'no_empty_operation': True}


def repeated_edit_probe(state, catalog, repository_root):
    edited, _ = apply_action(state, request_fixture(state, action='edit_plan', step_changes=[{'step_id': 'outliers', 'parameter': 'method', 'value': 'iqr'}]), catalog, repository_root)
    view = project(edited, catalog, updated=True)['data']['payload']['plans'][0]
    method = next(item for step in view['steps'] if step['step_id'] == 'outliers' for item in step['parameters'] if item['name'] == 'method')
    ensure(method['editable'] and method['value'] == 'iqr', 'edited parameter became read only')
    _, snapshot = apply_action(edited, request_fixture(edited, step_changes=[{'step_id': 'outliers', 'parameter': 'method', 'value': 'zscore'}]), catalog, repository_root)
    ensure(snapshot['steps'][-1]['arguments']['method']['value'] == 'zscore', 'second edit failed')
    return {'second_edit_applied': True, 'final_revision': snapshot['plan_revision']}


def preserve_exclusion_probe(state, catalog, repository_root):
    edited, _ = apply_action(state, request_fixture(state, action='edit_plan', excluded_step_ids=['outliers']), catalog, repository_root)
    _, snapshot = apply_action(edited, request_fixture(edited), catalog, repository_root)
    ensure(snapshot['excluded_step_ids'] == ['outliers'], 'omission cleared exclusion')
    return {'exclusion_preserved': True}


def explicit_null_probe(document, catalog, repository_root):
    document = deepcopy(document)
    document['inputs']['note'] = {'title': '추가 설명', 'description': '선택 입력', 'kind': 'parameter',
                                  'required': False, 'editable': True, 'value_schema': {'type': ['string', 'null']}}
    state = make_state(document, {'dataset': 'dataset-demo', 'note': None}, context_fixture())
    event = project(state, catalog)
    item = next(item for item in event['data']['payload']['plans'][0]['inputs'] if item['name'] == 'note')
    ensure(item['has_value'] is True and 'value' in item and item['value'] is None, 'null treated as missing')
    approved, _ = apply_action(state, request_fixture(state), catalog, repository_root)
    item = next(item for item in resolved_event(approved, catalog)['data']['payload']['approved_plan']['inputs'] if item['name'] == 'note')
    ensure('value' in item and item['value'] is None, 'null lost in approved view')
    return {'has_value': True, 'explicit_null_preserved': True}


def literal_control_probe(document, catalog, repository_root):
    document = deepcopy(document)
    step = document['steps'][-1]
    step['arguments']['method'] = {'source': 'literal', 'value': 'iqr'}
    step['parameter_controls'] = {'method': {'editable': True, 'value_schema': {'type': 'string', 'enum': ['iqr', 'zscore']}}}
    state = make_state(document, {'dataset': 'dataset-demo'}, context_fixture())
    _, snapshot = apply_action(state, request_fixture(state, step_changes=[{'step_id': 'outliers', 'parameter': 'method', 'value': 'zscore'}]), catalog, repository_root)
    ensure(snapshot['steps'][-1]['arguments']['method']['value'] == 'zscore', 'fixed parameter edit failed')
    return {'declared_literal_control': True, 'approved_value': 'zscore'}


def local_execution_probe(document, catalog, repository_root):
    import pandas as pd

    with TemporaryDirectory(prefix='dtest-plan-contract-') as directory:
        path = Path(directory) / "quality's.parquet"
        pd.DataFrame({'temperature': [10.0, 11.0, 10.0, 12.0, 11.0, 10.0, 12.0, 11.0, 10.0, 12.0, 11.0, 100.0],
                      'label': ['normal'] * 11 + ['outlier']}).to_parquet(path)
        context = context_fixture()
        context['dataset_refs']['dataset-demo']['resolved_path'] = str(path)
        state = make_state(document, {'dataset': 'dataset-demo'}, context)
        request = request_fixture(state, step_changes=[{'step_id': 'outliers', 'parameter': 'method', 'value': 'iqr'}])
        _, snapshot = apply_action(state, request, catalog, repository_root)
        initial = submission(snapshot)
        operation, _ = next_operation(snapshot, ['load', 'profile', 'statistics'], {'inspect_outliers': True}, expected_version=4, next_sequence=3)
        namespace = {}
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            for body in [initial['operation'], operation]:
                for step in body['spec']['steps']:
                    exec(compile(step['payload']['source']['content'], '<local-contract-fixture>', 'exec'), namespace)
        ensure(namespace[variable('load')].shape == (12, 2), 'wrong load result')
        ensure(namespace[variable('profile')]['profile']['row_count'] == 12, 'profile result missing')
        ensure(namespace[variable('outliers')]['outlier_indices'] == [11], 'outlier result incorrect')
        lines = [line.removeprefix('DTEST_OBSERVATION ') for line in stdout.getvalue().splitlines() if line.startswith('DTEST_OBSERVATION ')]
        observations = [json.loads(line) for line in lines]
        ensure(len(observations) == 4, 'missing text observations')
        ensure(observations[0]['summary']['truncated'] is True, 'full dataset dumped')
        return {'rows': 12, 'columns': 2, 'operation_sequences': [[0, 1, 2], [3]], 'outlier_indices': [11],
                'text_observations': 4, 'dataframe_preview_rows': 5,
                'note': 'Same compiler and frozen repository functions; resource resolver uses a temporary local file, not the illustrative Executor path.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository-root', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, default=DEST)
    arguments = parser.parse_args()
    arguments.output_dir.mkdir(parents=True, exist_ok=True)
    result = probe(arguments.repository_root.resolve(), arguments.output_dir)
    (arguments.output_dir / 'validation.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'passed': result['passed'], 'checks': len(result['checks']),
                      'failures': [item for item in result['checks'] if not item['passed']]}, ensure_ascii=False))
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
