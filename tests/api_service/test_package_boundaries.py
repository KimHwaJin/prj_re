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


@pytest.mark.parametrize(
    "package,forbidden",
    [
        (
            "dtest/agent_service",
            ("dtest.api_service", "dtest.application", "dtest.worker_service"),
        ),
        (
            "dtest/infrastructure",
            (
                "dtest.api_service",
                "dtest.application",
                "dtest.worker_service",
                "dtest.agent_service",
            ),
        ),
        (
            "dtest/contracts",
            (
                "dtest.api_service",
                "dtest.application",
                "dtest.worker_service",
                "dtest.agent_service",
                "dtest.infrastructure",
                "fastapi",
                "langgraph",
                "langchain",
            ),
        ),
        (
            "dtest/application",
            (
                "dtest.api_service",
                "dtest.worker_service",
                "dtest.agent_service",
                "fastapi",
                "starlette",
                "langgraph",
                "langchain",
            ),
        ),
        (
            "dtest/api_service/http/v1/routes",
            (
                "dtest.infrastructure.database.models",
                "dtest.infrastructure.database.repositories",
                "sqlalchemy",
            ),
        ),
        ("dtest/api_service", ("dtest.agent_service", "dtest.worker_service")),
    ],
)
def test_service_dependency_boundaries(package, forbidden):
    sources = list((SRC / package).rglob("*.py"))
    assert sources, f"Missing package {package}"
    errors = []
    for path in sources:
        for name in imports(path):
            if any(
                name == prefix or name.startswith(prefix + ".")
                for prefix in forbidden
            ):
                errors.append(f"{path.relative_to(SRC)}: {name}")
    assert not errors, "\n".join(errors)


def test_agent_graph_runs_with_api_imports_blocked(tmp_path):
    # A fresh interpreter detects indirect/dynamic imports, not only AST imports.
    program = """
import asyncio, importlib.abc, sys
class NoAPI(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'dtest.api_service' or fullname.startswith('dtest.api_service.'):
            raise AssertionError('Agent imported API: ' + fullname)
sys.meta_path.insert(0, NoAPI())
from dtest.devtools.analysis.runtime import local_runtime, local_input
from dtest.agent_service.agents.analysis.planning.graph import build_planning_graph
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
    assert not any(n == 'dtest.api_service' or n.startswith('dtest.api_service.') for n in sys.modules)
asyncio.run(main())
"""
    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(SRC)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
