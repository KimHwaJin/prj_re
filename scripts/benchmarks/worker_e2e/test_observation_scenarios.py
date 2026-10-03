"""Fixture contract, bounded evidence integrity, and old capture compatibility."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from jsonschema_rs import Draft202012Validator
import pytest
from observation_scenarios import plan_document, scenario
from service_contracts.workflow_validation import workflow_schema
from integrations.executor.observations import read_operation_observations

ROOT = Path(__file__).resolve().parents[3]


def test_expanded_plan_uses_registered_tools_and_valid_workflow_contract():
    document = json.loads((ROOT/'src/agent_service/agents/analysis/planning/fixtures/quality-review.json').read_text())
    original = deepcopy(document)
    for profile in ('standard', 'large20'):
        result = plan_document(document, profile)
        assert not list(Draft202012Validator(workflow_schema()).iter_errors(result))
        assert [step['id'] for step in result['steps']] == list(scenario(profile).step_ids)
        assert {step['tool_id'] for step in result['steps']} <= {step['tool_id'] for step in document['steps']}
    assert document == original


def test_large_stdout_is_verified_bounded_and_summary_survives(tmp_path):
    spec = importlib.util.spec_from_file_location('observation_fixture_executor', ROOT/'scripts/benchmarks/executor_throughput/mock_executor.py')
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    execution, operation, step = map(str, (uuid4(), uuid4(), uuid4()))
    # This source would fail if executed. The HTTP fixture only inspects/writes it.
    code = "raise RuntimeError('Never execute fixture source')\nobs={'step_id':'load','summary':{}}"
    submitted = [{'sequence':0, 'step_id':step, 'payload':{'source':{'content':code}}, 'lineage':{'tool_name':'data_load'}}]
    results = module.write_results(tmp_path, execution, operation, submitted, log_bytes=65536)
    event = {'execution_id':execution, 'payload':{'operation':{'id':operation}, 'status':'SUCCEEDED', 'step_results':results}}
    settings = SimpleNamespace(executor_shared_result_root=tmp_path, agent_observation_max_chars=16000)
    expected = [{'sequence':0, 'executor_step_id':step, 'plan_step_id':'load', 'tool_id':'data_load'}]
    facts = read_operation_observations(settings, event, expected)
    assert facts[0]['summary']['type'] == 'service_fixture'
    assert len(facts[0]['text'][0]) == 16000 and facts[0]['status'] == 'SUCCEEDED'
    output = next(tmp_path.rglob('stdout.txt'));original = output.read_bytes()
    output.write_bytes(b'X'+original[1:])
    with pytest.raises(ValueError, match='integrity'):
        read_operation_observations(settings, event, expected)
