"""Pure plan editing rules. API validates before consuming a resume token."""
from copy import deepcopy
from hashlib import sha256
import json
from uuid import uuid4

from jsonschema_rs import Draft202012Validator

from service_contracts.plan_interaction import PlanAction
from service_contracts.workflow_validation import validate, bindings


class PlanReviewError(ValueError):
    """Safe, user-correctable form error."""


def require(condition, message):
    if not condition:
        raise PlanReviewError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def new_review(document, values, catalog, policy):
    errors = validate(document, catalog)
    require(not errors, '; '.join(errors[:8]))
    document = deepcopy(document)
    execution = {'mode': 'MULTI', 'repair_level': 0, 'max_repair_attempts': 0, 'review_mode': 'decision_boundary'}
    execution.update(document.get('execution', {}))
    document['execution'] = execution
    require(execution['repair_level'] <= policy['repair_level_limit'], 'Repair level exceeds service limit')
    require(execution['max_repair_attempts'] <= policy['max_repair_attempts_limit'], 'Repair attempts exceed service limit')
    require(execution['mode'] != 'SINGLE' or (execution['repair_level'] == 0 and execution['max_repair_attempts'] == 0), 'SINGLE does not support repair')
    require(execution['mode'] != 'SINGLE' or execution['review_mode']=='decision_boundary','SINGLE cannot review between Tools')
    initial = {k: v['default'] for k, v in document['inputs'].items() if 'default' in v}
    origins = {k: 'workflow_default' for k in initial}
    for key, value in values.items():
        require(key in document['inputs'], 'Unknown proposed input')
        require(Draft202012Validator(document['inputs'][key]['value_schema']).is_valid(value), 'Invalid proposed input')
        initial[key], origins[key] = value, 'agent'
    decisions = {d['id']: d for d in document['decisions']}
    editable = {}
    for step in document['steps']:
        editable[step['id']] = {}
        for key, binding in step['arguments'].items():
            control = step.get('parameter_controls', {}).get(key)
            schema = decisions[binding['decision_id']]['output_schema'] if binding['source'] == 'agent_decision' else None
            if control is not None:
                if not control['editable']:
                    schema = None
                elif schema is not None:
                    schema = {'allOf': [schema, control['value_schema']]}
                else:
                    schema = control['value_schema']
            if schema is not None:
                editable[step['id']][key] = schema
    return {'plan_id': str(uuid4()), 'plan_revision': 1, 'document': document,
            'input_values': initial, 'input_origins': origins, 'editable_parameters': editable,
            'excluded_step_ids': [], 'user_actions': [], 'catalog': deepcopy(catalog),
            'policy': deepcopy(policy), 'consumed': False}


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
                'tool_sources': {s['tool_id']: sources[s['tool_id']] for s in steps},
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
    snapshot['approval_sha256'] = sha256(canonical(snapshot).encode()).hexdigest()
    return snapshot
