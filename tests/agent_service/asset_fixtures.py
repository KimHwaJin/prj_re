"""Two independent deployed-asset fixtures; no shipped Tool/Skill dependencies."""
from copy import deepcopy
from pathlib import Path
import json
import yaml

from dtest.agent_service.agents.analysis.planning.catalog import AssetCatalog
from dtest.agent_service.agents.analysis.workflow.tools.generate_tool_registry import build_registry, write_registry

CASES = {
    'inventory': {'skill': 'warehouse', 'reader': 'decode_lots', 'reader_arg': 'document',
        'processor': 'summarize_lots', 'object_arg': 'batch', 'option': 'multiplier',
        'selector': 'batches', 'result': 'stock', 'nested': True},
    'billing': {'skill': 'accounting', 'reader': 'extract_receipts', 'reader_arg': 'encoded',
        'processor': 'quote_invoice', 'object_arg': 'lines', 'option': 'coefficient',
        'selector': 'receipts', 'result': 'invoice_total', 'nested': False},
}


def assets(root: Path, name: str):
    case = deepcopy(CASES[name])
    (root / 'tools').mkdir(parents=True)
    (root / 'skills').mkdir()
    result = "{'stock': {'total': sum(batch) * multiplier}}" if case['nested'] else "{'invoice_total': sum(lines) * coefficient}"
    source = f'''raise RuntimeError('Catalog must never import this module')

def {case['reader']}({case['reader_arg']}: str) -> dict:
    """Decode the supplied JSON array of numbers. Returns {{{case['selector']}: list[number]}}.
    Accepts a Workflow parameter, never a runtime file path.
    """
    import json
    return {{'{case['selector']}': json.loads({case['reader_arg']})}}

def {case['processor']}({case['object_arg']}, {case['option']}=1) -> dict:
    """Sum the decoded number list and multiply it by {case['option']}.
    Returns {case['result']} as {'an object with total' if case['nested'] else 'a number'}.
    """
    return {result}

def _private_helper():
    raise RuntimeError('Never register helpers')
'''
    (root / 'tools' / 'business_functions.py').write_text(source, encoding='utf-8')
    registry = build_registry(root / 'tools')
    registry['tools'][case['reader']]['parameter_bindings'] = {case['reader_arg']:
        {'allowed_sources': ['workflow_input'], 'input_kind': 'parameter', 'required': True}}
    registry['tools'][case['processor']]['parameter_bindings'] = {case['object_arg']:
        {'allowed_sources': ['step_output'], 'required': True}}
    registry['tools'][case['processor']]['parameter_controls'] = {case['option']:
        {'title': '배율', 'description': '최종 승인 배율', 'editable': True,
         'value_schema': {'type': 'integer', 'minimum': 1, 'maximum': 10}}}
    write_registry(registry, root / 'tools' / 'tool_registry.yaml')
    (root / 'skills' / 'operations.md').write_text(
        f"# {name}\nUse {case['reader']} to decode the user's JSON array, then {case['processor']}. "
        f"Pass the returned {case['selector']} list to {case['object_arg']}.\n", encoding='utf-8')
    (root / 'skills' / 'skill_index.yaml').write_text(yaml.safe_dump({'skills': {case['skill']:
        {'source': 'operations.md', 'description': f'{name} numerical operations',
         'tools': [{'tool': case['reader']}, {'tool': case['processor']}]}}}))
    return AssetCatalog(root), case


def definition(case, *, conditional=True):
    step = {'id': 'calculate', 'skill_id': case['skill'], 'tool_id': case['processor'],
        'description': 'Apply the approved multiplier', 'depends_on': ['decode'],
        'arguments': {case['object_arg']: {'source': 'step_output', 'step_id': 'decode', 'selector': [case['selector']]}}}
    result_path = [case['result'], 'total'] if case['nested'] else [case['result']]
    doc = {'schema_version': '2.0-draft', 'workflow_id': 'independent_pool', 'definition_version': 1,
        'name': 'Independent asset contract', 'description': 'No shipped assets', 'goal': 'Compute approved total',
        'tags': [], 'inputs': {'payload': {'title': '수열', 'description': 'JSON encoded numbers',
            'kind': 'parameter', 'required': True, 'editable': True, 'value_schema': {'type': 'string'}}},
        'steps': [{'id': 'decode', 'skill_id': case['skill'], 'tool_id': case['reader'],
            'description': 'Read the approved input', 'depends_on': [],
            'arguments': {case['reader_arg']: {'source': 'workflow_input', 'name': 'payload'}}}, step],
        'decisions': [], 'execution': {'mode': 'MULTI', 'repair_level': 0,
            'max_repair_attempts': 0, 'review_mode': 'decision_boundary'},
        'expected_outputs': [{'id': 'approved_total', 'kind': 'analysis_result', 'description': 'Approved total',
            'required': True, 'format': 'native', 'source': {'source': 'step_output', 'step_id': 'calculate', 'selector': result_path}},
            {'id': 'summary_report', 'kind': 'report', 'description': 'Explain observed result', 'required': True,
             'format': 'markdown', 'source': {'source': 'agent_report', 'evidence_steps': ['decode', 'calculate']}}]}
    if conditional:
        optional = deepcopy(step)
        optional.update(id='optional', description='Optional second calculation', depends_on=['decode', 'calculate'],
                        when={'op': 'eq', 'left': {'source': 'agent_decision', 'decision_id': 'continue_optional'}, 'right': {'source': 'literal', 'value': True}})
        optional['arguments'][case['option']] = {'source': 'literal', 'value': 2}
        doc['steps'].append(optional)
        doc['decisions'] = [{'id': 'continue_optional', 'instruction': 'Proceed only if the observed total is positive.',
                             'after_steps': ['calculate'], 'output_schema': {'type': 'boolean'}}]
    return doc
