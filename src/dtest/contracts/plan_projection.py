"""Code-free views of checkpointed plans; no asset or database I/O."""
from dtest.contracts.plan_interaction import (InputView, ParameterView, StepView, OutputView, PlanView, SkillView, DecisionView, PolicyView)
from dtest.contracts import workflow_validation as workflow_contract

def needed_decisions(state):
    used = {binding['decision_id'] for step in state['document']['steps']
            if step['id'] not in state['excluded_step_ids']
            for binding in workflow_contract.bindings([step['arguments'], step.get('when')])
            if binding['source'] == 'agent_decision'}
    return [item for item in state['document']['decisions'] if item['id'] in used]


def plan_view(state):
    catalog = state["catalog"]
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
            policy = catalog['tools'][step['tool_id']].get('parameter_controls', {}).get(name, {})
            parameter = parameters[-1]
            parameter.title = policy.get('title')
            parameter.description = policy.get('description')
            parameter.has_value = source == 'literal'
            parameter.origin = state.get('parameter_origins', {}).get(step['id'], {}).get(name,
                'agent' if source == 'literal' else 'unresolved')
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
        execution_kind=state.get('execution_kind','registered'), workflow_eligible=state.get('workflow_eligible',True),
        approval_mode=state.get('approval_mode','user'), catalog_reference=state.get("catalog_reference"),
        skills=[SkillView(skill_id=id, name=id, description=catalog['skills'][id].get('description', id))
                for id in dict.fromkeys(step['skill_id'] for step in document['steps'])],
        decisions=[DecisionView(decision_id=item['id'], evidence_steps=item['after_steps'],
                               guidance=item['instruction'], value_schema=item['output_schema']) for item in needed_decisions(state)],
        execution=PolicyView(**document['execution'],
            **state['policy']), outputs=outputs)
    result = plan.model_dump(mode='json', exclude_none=True)
    for field in result['inputs']:
        if field['has_value'] and state['input_values'][field['name']] is None:
            field['value'] = None
    for step in result['steps']:
        for parameter in step['parameters']:
            if parameter['kind'] == 'literal' and next(s for s in document['steps'] if s['id'] == step['step_id'])['arguments'][parameter['name']]['value'] is None:
                parameter['value'] = None
    return result
