"""Pure plan editing rules. API validates before consuming a resume token."""
from copy import deepcopy
from hashlib import sha256
import json
from uuid import uuid4

from jsonschema_rs import Draft202012Validator

from service_contracts.plan_interaction import PlanAction
from service_contracts.workflow_validation import validate, bindings
from service_contracts.tool_parameters import materialize_defaults, editable_schema, value_schema


class PlanReviewError(ValueError):
    """Safe, user-correctable form error."""


def require(condition, message):
    if not condition:
        raise PlanReviewError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def new_review(document, values, catalog, policy):
    if document.get('workflow_version') == '2.0':
        from service_contracts.workflow_standard import normalize
        document = normalize(document, catalog)
    errors = validate(document, catalog)
    require(not errors, '; '.join(errors[:8]))
    document, parameter_origins = materialize_defaults(document, catalog)
    errors = validate(document, catalog)
    require(not errors, '; '.join(errors[:8]))
    execution = {'mode': 'MULTI', 'repair_level': policy.get('default_repair_level',0),
                 'max_repair_attempts': 0, 'review_mode': 'decision_boundary'}
    declared=document.get('execution', {})
    execution.update(declared)
    if 'max_repair_attempts' not in declared and execution['repair_level']>0:
        execution['max_repair_attempts']=policy.get('default_repair_attempts',0)
    document['execution'] = execution
    require(execution['repair_level'] <= policy['repair_level_limit'], 'Repair level exceeds service limit')
    require(execution['max_repair_attempts'] <= policy['max_repair_attempts_limit'], 'Repair attempts exceed service limit')
    require(execution['mode'] != 'SINGLE' or (execution['repair_level'] == 0 and execution['max_repair_attempts'] == 0), 'SINGLE does not support repair')
    require(execution['mode'] != 'SINGLE' or execution['review_mode']=='decision_boundary','SINGLE cannot review between Tools')
    initial = {k: v['default'] for k, v in document['inputs'].items() if 'default' in v}
    origins = {k: 'workflow_default' for k in initial}
    for key, value in values.items():
        require(key in document['inputs'], f'Unknown proposed input: {key}')
        require(Draft202012Validator(document['inputs'][key]['value_schema']).is_valid(value),
                f'Invalid proposed input: {key}. Follow its value_schema; omit unresolved input_values keys. '
                'null is a supplied value, not a missing input.')
        initial[key], origins[key] = value, 'agent'
    decisions = {d['id']: d for d in document['decisions']}
    editable = {}
    for step in document['steps']:
        editable[step['id']] = {}
        for key, binding in step['arguments'].items():
            schema = editable_schema(catalog['tools'][step['tool_id']], step, key, decisions)
            if schema is not None:
                editable[step['id']][key] = schema
    validate_parameter_inputs(document, catalog, initial)
    return {'plan_id': str(uuid4()), 'plan_revision': 1, 'document': document,
            'parameter_origins': parameter_origins,
            'input_values': initial, 'input_origins': origins, 'editable_parameters': editable,
            'excluded_step_ids': [], 'user_actions': [], 'catalog': deepcopy(catalog),
            'policy': {k:deepcopy(v) for k,v in policy.items() if not k.startswith('default_')}, 'consumed': False}


def validate_parameter_inputs(document, catalog, values):
    for step in document['steps']:
        tool = catalog['tools'][step['tool_id']]
        for name, binding in step['arguments'].items():
            if binding['source'] == 'workflow_input' and binding['name'] in values:
                require(Draft202012Validator(value_schema(tool, step, name)).is_valid(values[binding['name']]),
                        f'Tool input value violates its parameter schema: {step["id"]}.{name}')


def visible_datasets(datasets, context):
    visible = {}
    for key, item in datasets.items():
        scope = item.get('scope')
        allowed = scope == 'GLOBAL' or (
            item.get('owner_user_id') == context.get('user_id') and
            (scope == 'USER' or (item.get('project_id') == context.get('project_id') and
             (scope == 'PROJECT' or (scope == 'SESSION' and item.get('session_id') == context.get('session_id'))))))
        if allowed:
            visible[key] = item
    return visible


def patch_review(review, raw_action, *, datasets, context):
    action = PlanAction.model_validate(raw_action)
    require(not review['consumed'], 'Plan was already approved')
    require(action.plan_id == review['plan_id'], 'Unknown plan')
    require(action.plan_revision == review['plan_revision'], 'Stale plan revision; refresh the plan')
    result = deepcopy(review)
    document = result['document']
    for key, value in action.input_values.items():
        field = document['inputs'].get(key)
        require(field is not None and field['editable'], 'Input is unknown or read only')
        require(Draft202012Validator(field['value_schema']).is_valid(value), 'Input value violates its schema')
        result['input_values'][key], result['input_origins'][key] = value, 'user'
    steps = {s['id']: s for s in document['steps']}
    excluded = action.excluded_step_ids if 'excluded_step_ids' in action.model_fields_set else result['excluded_step_ids']
    require(len(excluded) == len(set(excluded)), 'Duplicate excluded step')
    require(set(excluded) <= steps.keys() and len(excluded) < len(steps), 'Unknown excluded step or all steps excluded')
    result['excluded_step_ids'] = sorted(excluded)
    for key, step in steps.items():
        if key not in excluded:
            require(not set(step['depends_on']).intersection(excluded), 'Exclusion breaks a retained step dependency')
    seen = set()
    for change in action.step_changes:
        key = (change.step_id, change.parameter)
        require(key not in seen, 'Duplicate parameter change')
        seen.add(key)
        require(change.step_id in steps and change.step_id not in excluded, 'Changed step is unknown or excluded')
        schema = result['editable_parameters'].get(change.step_id, {}).get(change.parameter)
        require(schema is not None, 'Parameter is read only')
        require(Draft202012Validator(schema).is_valid(change.value), 'Parameter value violates its schema')
        steps[change.step_id]['arguments'][change.parameter] = {'source': 'literal', 'value': change.value}
        result.setdefault('parameter_origins', {}).setdefault(change.step_id, {})[change.parameter] = 'user'
    document['execution'].update(action.execution_overrides.model_dump(exclude_none=True))
    policy = result['policy']
    require(document['execution']['mode'] in policy['allowed_modes'], 'Execution mode is not allowed')
    require(0 <= document['execution']['repair_level'] <= policy['repair_level_limit'], 'Repair level exceeds service limit')
    require(0 <= document['execution']['max_repair_attempts'] <= policy['max_repair_attempts_limit'], 'Repair attempts exceed service limit')
    require(document['execution']['mode'] != 'SINGLE' or (document['execution']['repair_level'] == 0 and document['execution']['max_repair_attempts'] == 0), 'SINGLE does not support repair')
    require(document['execution']['mode'] != 'SINGLE' or document['execution']['review_mode']=='decision_boundary','SINGLE cannot review between Tools')
    errors = validate(document, result['catalog'])
    require(not errors, '; '.join(errors[:8]))
    used = {b['decision_id'] for s in document['steps'] if s['id'] not in excluded
            for b in bindings([s['arguments'], s.get('when')]) if b['source'] == 'agent_decision'}
    for decision in document['decisions']:
        if decision['id'] in used:
            require(not set(decision['after_steps']).intersection(excluded), 'Exclusion removes decision evidence')
    validate_parameter_inputs(document, result['catalog'], result['input_values'])
    accessible = visible_datasets(datasets, context)
    for key, field in document['inputs'].items():
        present = key in result['input_values']
        if action.action == 'approve_plan':
            require(not field['required'] or present, f'Required input is missing: {key}')
        if present:
            value = result['input_values'][key]
            require(Draft202012Validator(field['value_schema']).is_valid(value), 'Final input violates its schema')
            if field['kind'] == 'data_reference':
                require(isinstance(value, str) and value in accessible, 'Dataset reference is unknown or inaccessible')
    if action.action == 'approve_plan':
        for step in document['steps']:
            if step['id'] in excluded:
                continue
            tool = result['catalog']['tools'][step['tool_id']]
            for name, binding in step['arguments'].items():
                policy = tool.get('parameter_bindings', {}).get(name, {})
                required = policy.get('required') or name in tool['required_parameters']
                if required and binding['source'] == 'workflow_input':
                    require(binding['name'] in result['input_values'],
                            f"Required Tool input is missing: {step['id']}.{name}")
    if (document != review['document'] or result['input_values'] != review['input_values'] or
            result['excluded_step_ids'] != review['excluded_step_ids']):
        result['plan_revision'] += 1
    result['user_actions'].append(action.model_dump(mode='json', exclude_unset=True))
    result['consumed'] = action.action == 'approve_plan'
    return result


def freeze_approval(review, sources, skill_sources, context, asset_revision, datasets=None):
    require(review['consumed'], 'Approval is required')
    steps = [s for s in review['document']['steps'] if s['id'] not in review['excluded_step_ids']]
    snapshot = {'schema_version': 'plan-snapshot-1-draft', 'run_id': context['public_run_id'],
                'plan_id': review['plan_id'], 'plan_revision': review['plan_revision'],
                'document': deepcopy(review['document']), 'steps': deepcopy(steps),
                'input_values': deepcopy(review['input_values']), 'execution': deepcopy(review['document']['execution']),
                'excluded_step_ids': review['excluded_step_ids'], 'user_actions': review['user_actions'],
                'asset_revision': asset_revision,
                'tool_sources': {s['tool_id']: {**sources, **review.get('local_sources', {})}[s['tool_id']] for s in steps},
                'skill_sources': {s['skill_id']: skill_sources[s['skill_id']] for s in steps},
                'context': {k: context[k] for k in ('user_id', 'project_id', 'session_id', 'public_run_id')}}
    # Freeze the trusted resolution as well as the public ID, so a later config
    # edit cannot redirect an already approved analysis to a different file.
    accessible = visible_datasets(datasets or {}, context)
    snapshot['dataset_bindings'] = {}
    # Pin the session's kernel and service data root before any external submission.
    for key in ('kernel_profile','dataset_output_dir'):
        if key in context:
            snapshot['context'][key] = context[key]
    for name, field in review['document']['inputs'].items():
        if field['kind'] == 'data_reference' and name in review['input_values']:
            reference = review['input_values'][name]
            require(reference in accessible, 'Approved dataset is no longer accessible')
            snapshot['dataset_bindings'][name] = {'dataset_id': reference, **deepcopy(accessible[reference])}
    if review.get('execution_kind'):
        snapshot['execution_kind'] = review['execution_kind']
        snapshot['workflow_eligible'] = review['workflow_eligible']
        snapshot['approval_mode'] = review.get('approval_mode', 'user')
    snapshot['approval_sha256'] = sha256(canonical(snapshot).encode()).hexdigest()
    return snapshot
