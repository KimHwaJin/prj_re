"""Prevent API/Agent import cycles and accidental runtime resource coupling."""
import ast
import os
from pathlib import Path
import subprocess
import sys

import pytest

SRC = Path(__file__).resolve().parents[2]


def imports(path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module:
            yield node.module
        elif isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)


@pytest.mark.parametrize('package', ['agent_service', 'integrations', 'service_contracts', 'service_runtime', 'service_auth'])
def test_shared_and_agent_packages_do_not_depend_on_api(package):
    forbidden = {'app', 'api_service'}
    if package != 'agent_service':
        forbidden.add('agent_service')
    errors = []
    for path in (SRC / package).rglob('*.py'):
        if 'tests' in path.parts:
            continue
        for name in imports(path):
            if name.split('.')[0] in forbidden:
                errors.append(f'{path.relative_to(SRC)}: {name}')
    assert not errors, '\n'.join(errors)


def test_only_composition_adapters_import_agent_implementation():
    allowed = {
        'services/agent_graph_service.py',
        'agent_worker/graph_provider.py',
        'agent_worker/worker_main.py',
    }
    errors = []
    for path in (SRC / 'api_service').rglob('*.py'):
        if 'test' in path.parts or path.relative_to(SRC / 'api_service').as_posix() in allowed:
            continue
        for name in imports(path):
            if name.split('.')[0] == 'agent_service':
                errors.append(f'{path.relative_to(SRC)}: {name}')
    assert not errors, '\n'.join(errors)


def test_agent_graph_runs_with_api_imports_blocked(tmp_path):
    # A fresh interpreter detects indirect/dynamic imports, not only AST imports.
    program = '''
import asyncio, importlib.abc, sys
class NoAPI(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'api_service', 'app'}:
            raise AssertionError('Agent imported API: ' + fullname)
sys.meta_path.insert(0, NoAPI())
from agent_config import load_agent_settings
from agent_service.agents.analysis.dependencies import create_llm_dependencies
from agent_service.agents.analysis.graph import build_analysis_workflow_graph
from agent_service.runtime.langgraph.checkpointer import create_checkpointer
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
settings = load_agent_settings({'MODEL_PROVIDER':'mock', 'DATA_MOCK':'true',
    'DEMO_ARTIFACTS_ENABLED':'false', 'EXECUTOR_SUBMIT_ENABLED':'false', 'EXECUTOR_SOURCE_TYPE':'INLINE'})
async def main():
    graph = build_analysis_workflow_graph(create_llm_dependencies(settings), settings, checkpointer=InMemorySaver())
    cfg = {'configurable': {'thread_id': 'isolated-agent'}}
    state = await graph.ainvoke({'user_request':'boundary smoke', 'session_id':'isolated-agent',
        'user_id':'user', 'project_id':'project'}, cfg)
    for answer in ['mock', {'objective':'EDA'}, {'candidate_number':1}, {'approved':True}]:
        state = await graph.ainvoke(Command(resume=answer), cfg)
    assert state['execution_steps']
    assert state['executor_submit_response']['skipped']
    assert not any(n == 'api_service' or n.startswith('api_service.') for n in sys.modules)
asyncio.run(main())
'''
    result = subprocess.run([sys.executable, '-c', program], cwd=tmp_path,
        env={**os.environ, 'PYTHONPATH':str(SRC)}, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
