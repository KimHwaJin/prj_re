"""Offline wheel smoke: run with python -I to exclude checkout imports.

Unpacks into a disposable directory; no dependency install or external service
calls. Existing interpreter dependencies must match the project lock.
"""
import asyncio
from importlib.resources import files
import json
from pathlib import Path
import sys
import tempfile
from uuid import uuid4
from zipfile import ZipFile

import argparse
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('wheel', type=Path)
wheel = parser.parse_args().wheel.resolve()
temporary = tempfile.TemporaryDirectory(prefix='dtest-layout-installed-')
installed = Path(temporary.name)
with ZipFile(wheel) as archive:
    names = archive.namelist()
    assert not any('/tests/' in n or n.startswith('app/test/') for n in names)
    assert not any(n.startswith(('app/agents/', 'app/graphs/', 'app/worker_past/')) for n in names)
    assert not any(n.startswith(('agent_service/agents/analysis/prompts/', 'agent_service/agents/analysis/resources/')) for n in names)
    assert 'app/workflow/workflows/workflow_lifecycle.md' in names
    assert 'app/workflow/skills/generate_skill_index.py' in names
    assert 'app/workflow/tools/generate_tool_registry.py' in names
    archive.extractall(installed)
sys.path.insert(0, str(installed))

import agent_service.agents.analysis.graph as graph_module
from agent_service.agents.analysis.dependencies import create_llm_dependencies
from agent_service.agents.analysis.resource_paths import TOOLS_ROOT, SKILL_INDEX_PATH, TOOL_REGISTRY_PATH
from agent_config import load_agent_settings
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from service_settings import load_settings
from service_bootstrap import create_app
import devtools.analysis.cli

assert Path(graph_module.__file__).is_relative_to(installed)
assert SKILL_INDEX_PATH.is_file() and TOOL_REGISTRY_PATH.is_file()
assert (TOOLS_ROOT / 'eda/profile_data.py').is_file()
roles = ('routing', 'intent_classifier', 'skill_selector', 'workflow_generator',
         'conditional_decider', 'faq', 'report_writer')
for role in roles:
    prompt = files(f'agent_service.agents.analysis.agent_builders.{role}').joinpath('prompt.md')
    assert prompt.read_text(encoding='utf-8').strip()
# Construction only: exercise every production builder without calling a model.
production_settings = load_agent_settings({'MODEL_PROVIDER': 'openai_compatible',
    'MODEL_NAME': 'package-smoke', 'MODEL_API_KEY': 'test-key',
    'API_BASE_URL': 'http://llm.invalid/v1'})
production_deps = create_llm_dependencies(production_settings)
assert production_deps.report_agent is not None
assert production_deps.skill_selector_agent is not None
settings = load_settings(config={'MODEL_PROVIDER':'mock', 'AGENT_WORKER_ENABLED':False,
    'EVENT_WORKER_ENABLED':False, 'TASK_RECONCILER_ENABLED':False}, environ={})
app = create_app(settings)
paths = app.openapi()['paths']
assert any(p.endswith('/runs') for p in paths)
agent_settings = load_agent_settings({'MODEL_PROVIDER':'mock', 'DATA_MOCK':'true',
    'DEMO_ARTIFACTS_ENABLED':'false', 'EXECUTOR_SUBMIT_ENABLED':'false', 'EXECUTOR_SOURCE_TYPE':'INLINE'})

async def smoke():
    graph = graph_module.build_analysis_workflow_graph(create_llm_dependencies(agent_settings), agent_settings, checkpointer=InMemorySaver())
    session = str(uuid4())
    cfg={'configurable':{'thread_id':session}}
    state = await graph.ainvoke({'user_request':'wheel smoke', 'session_id':session,
        'user_id':str(uuid4()), 'project_id':str(uuid4())}, cfg)
    for value in ['mock', {'objective':'EDA'}, {'candidate_number':1}, {'approved':True}]:
        state = await graph.ainvoke(Command(resume=value), cfg)
    assert state['execution_steps']
    assert state['executor_submit_response']['skipped']
    return len(state['execution_steps'])

print(json.dumps({'wheel':wheel.name, 'source_checkout_imported':False,
    'api_openapi_paths':len(paths), 'mock_graph_execution_steps':asyncio.run(smoke()),
    'removed_agent_packages_in_wheel':False, 'original_workflow_package_present':True, 'tests_in_wheel':False, 'resources_present':True,
    'role_prompts_present':len(roles), 'production_builders_constructed':True}))
