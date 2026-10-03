"""Test-only evidence profiles; never service settings or registered Tool changes."""
from copy import deepcopy
from dataclasses import dataclass


@dataclass(frozen=True)
class ObservationScenario:
    step_ids: tuple[str, ...]
    operations: int
    reviews: int
    log_bytes: int


def scenario(profile='standard'):
    if profile == 'standard':
        return ObservationScenario(('load', 'profile', 'statistics', 'outliers'), 2, 1, 0)
    if profile == 'large20':
        return ObservationScenario(('load', *(f'profile_{i:02d}' for i in range(1, 20))), 20, 20, 65536)
    raise ValueError('Unknown observation fixture profile')


def plan_document(document, profile='standard'):
    """Use existing registered load/profile tools; no new execution function."""
    result = deepcopy(document)
    spec = scenario(profile)
    if profile == 'standard':
        return result
    prototype = next(step for step in result['steps'] if step['id'] == 'profile')
    steps = [result['steps'][0]]
    for i, name in enumerate(spec.step_ids[1:], 1):
        step = deepcopy(prototype)
        step.update(id=name, depends_on=[spec.step_ids[i-1]], description='Synthetic repeated profile for evidence-storage measurement')
        steps.append(step)
    result.update(workflow_id='service_observation_storage_fixture', steps=steps, decisions=[])
    result['execution'].update(review_mode='every_n_tools', review_interval_tools=1)
    result['expected_outputs'] = [
        {'id': 'profile_result', 'kind': 'analysis_result', 'description': 'Last synthetic profile',
         'required': True, 'source': {'source': 'step_output', 'step_id': spec.step_ids[-1], 'selector': ['profile']}, 'format': 'native'},
        {'id': 'report', 'kind': 'report', 'description': 'Synthetic evidence report', 'required': True,
         'source': {'source': 'agent_report', 'evidence_steps': list(spec.step_ids)}, 'format': 'markdown'}]
    return result
