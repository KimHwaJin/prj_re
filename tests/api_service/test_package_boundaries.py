"""Prevent API/Agent import cycles and accidental runtime resource coupling."""
import ast
import os
from pathlib import Path
import subprocess
import sys

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"


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
        'runs/runtime.py',
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
from devtools.analysis.runtime import local_runtime, local_input
from agent_service.agents.analysis.planning.graph import build_planning_graph
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
async def main():
    runtime=local_runtime()
    graph=build_planning_graph(runtime,checkpointer=InMemorySaver())
    value=local_input(runtime,'boundary smoke')
    cfg={'configurable':{'thread_id':value['session_id']}}
    state=await graph.ainvoke(value,cfg)
    plan=state['plan_views'][0]
    state=await graph.ainvoke(Command(resume={'resume':{'action':'approve_plan',
        'plan_id':plan['plan_id'],'plan_revision':plan['plan_revision']}}),cfg)
    assert state['approved_snapshot']['steps'] and state['final_response']['status']=='plan_approved'
    assert not any(n == 'api_service' or n.startswith('api_service.') for n in sys.modules)
asyncio.run(main())
'''
    result = subprocess.run([sys.executable, '-c', program], cwd=tmp_path,
        env={**os.environ, 'PYTHONPATH':str(SRC)}, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
