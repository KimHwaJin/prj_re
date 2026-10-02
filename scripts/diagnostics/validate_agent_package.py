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
    assert not any('/tests/' in n or n.startswith('api_service/test/') for n in names)
    assert not any(n.startswith('app/') for n in names)
    assert all(any(n.startswith(package + '/') for n in names) for package in ('api_service', 'agent_service', 'service_contracts', 'service_runtime', 'service_auth', 'integrations'))
    assert not any(n.startswith(('agent_service/agents/analysis/prompts/', 'agent_service/agents/analysis/resources/')) for n in names)
    assert 'agent_service/agents/analysis/workflow/workflows/workflow_lifecycle.md' in names
    assert 'agent_service/agents/analysis/workflow/skills/generate_skill_index.py' in names
    assert 'agent_service/agents/analysis/execution/grounding.py' in names
    assert 'agent_service/runtime/session_analysis.py' in names
    assert 'agent_service/middleware/session_analysis.py' in names
    assert 'agent_service/middleware/project_memory.py' in names
    assert 'service_contracts/project_memory.py' in names
    assert 'api_service/services/project_memory_policy.py' in names
    assert 'api_service/core/memory_store.py' in names
    assert 'service_contracts/memory_store.py' in names
    assert 'api_service/services/project_memory_service.py' not in names
    assert 'api_service/models/common/project_memory_model.py' not in names
    assert 'agent_service/middleware/planning_contract.py' in names
    assert 'agent_service/agents/analysis/agent_builders/conversation/planning_prompt.md' in names
    assert 'agent_service/agents/analysis/workflow/tools/generate_tool_registry.py' in names
    archive.extractall(installed)
sys.path.insert(0, str(installed))

import agent_service.agents.analysis.graph as graph_module
from agent_service.agents.analysis.dependencies import create_llm_dependencies
from agent_service.agents.analysis.resource_paths import (
    TOOLS_ROOT, SKILL_INDEX_PATH, TOOL_REGISTRY_PATH, SOURCE_ROOT,
    TOOL_SOURCE_PREFIXES, resolve_tool_source,
)
from agent_config import load_agent_settings
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from service_settings import load_settings
from service_bootstrap import create_app
import devtools.analysis.cli

assert Path(graph_module.__file__).is_relative_to(installed)
assert SKILL_INDEX_PATH.is_file() and TOOL_REGISTRY_PATH.is_file()
assert (TOOLS_ROOT / 'eda/profile_data.py').is_file()
assert SOURCE_ROOT == installed.resolve()
for prefix in TOOL_SOURCE_PREFIXES:
    assert resolve_tool_source(prefix + 'eda/profile_data.py', installed) == TOOLS_ROOT / 'eda/profile_data.py'
roles = ('routing', 'intent_classifier', 'skill_selector', 'workflow_generator',
         'conditional_decider', 'faq', 'report_writer', 'conversation', 'execution_review', 'execution_report', 'execution_repair', 'plan_revision')
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
from agent_service.factory import RoleAgent
from agent_service.context import AgentContext
for field in ('routing_agent', 'analysis_intent_agent', 'skill_selector_agent',
              'workflow_agent', 'faq_agent', 'report_agent', 'conditional_decision_agent'):
    role = getattr(production_deps, field)
    assert isinstance(role, RoleAgent)
    assert role.agent.checkpointer is False
from agent_service.agents.analysis.agent_builders.conversation.agent import build_agent as build_conversation
from agent_service.agents.analysis.planning.catalog import AssetCatalog
from agent_service.agents.analysis.dependencies import create_chat_model
conversation = build_conversation(create_chat_model(production_settings), AssetCatalog())
assert isinstance(conversation, RoleAgent) and conversation.agent.checkpointer is False
from agent_service.agents.analysis.agent_builders.execution_review.agent import build_agent as build_review
from agent_service.agents.analysis.agent_builders.execution_report.agent import build_agent as build_report
from agent_service.agents.analysis.agent_builders.execution_repair.agent import build_agent as build_repair
for builder in (build_review, build_report, build_repair):
    role = builder(create_chat_model(production_settings))
    assert isinstance(role, RoleAgent) and role.agent.checkpointer is False
from agent_service.agents.analysis.agent_builders.plan_revision.agent import build_agent as build_revision
revision = build_revision(create_chat_model(production_settings), AssetCatalog())
assert isinstance(revision, RoleAgent) and revision.agent.checkpointer is False
settings = load_settings(config={'MODEL_PROVIDER':'mock', 'AGENT_WORKER_ENABLED':False,
    'EVENT_WORKER_ENABLED':False, 'TASK_RECONCILER_ENABLED':False}, environ={})
app = create_app(settings)
paths = app.openapi()['paths']
assert app.openapi()['components']['securitySchemes']['LoginSession']['in'] == 'cookie'
assert '/api/v1/projects/{project_id}/memory' in paths
assert '/api/v1/projects/{project_id}/memory/{section}/{key}' in paths
assert '/api/v1/auth/login/sso' in paths and '/api/v1/auth/logout' in paths
assert any(p.endswith('/runs') for p in paths)
assert '/api/v1/tasks/{task_id}' in paths
assert '/api/v1/admin/tasks/{task_id}' in paths
for suffix in ('cancel', 'stream'):
    assert '/api/v1/tasks/{task_id}/' + suffix not in paths
    assert '/api/v1/sessions/{session_id}/runs/{run_id}/' + suffix in paths

assert '/api/v1/sessions/{session_id}/runs/{run_id}/resume' not in paths
assert '/api/v1/sessions/{session_id}/runs/stream' in paths
assert files('service_contracts').joinpath('resources/workflow-definition.schema.json').is_file()
assert files('agent_service.agents.analysis.agent_builders.conversation').joinpath('prompt.md').is_file()
assert files('agent_service.agents.analysis.planning').joinpath('fixtures/quality-review.json').is_file()

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
    from agent_service.agents.analysis.planning.runtime import PlanningRuntime
    from agent_service.agents.analysis.planning.graph import build_planning_graph
    planning_settings = load_settings(config={'MODEL_PROVIDER': 'mock', 'ANALYSIS_DATASETS': {
        'package-data': {'title':'Package fixture', 'scope':'GLOBAL', 'runtime_path':'/workspace/pv/example.parquet'}}}, environ={})
    runtime = PlanningRuntime(planning_settings.agent)
    graph = build_planning_graph(runtime, checkpointer=InMemorySaver())
    state = await graph.ainvoke({'user_id':str(uuid4()), 'project_id':str(uuid4()), 'session_id':session,
        'run_id':str(uuid4()), 'user_request':'품질 분석 계획', 'model_selection':runtime.models.select().model_dump()}, cfg)
    plan = state['plan_views'][0]
    state = await graph.ainvoke(Command(resume={'resume': {'action':'approve_plan',
        'plan_id':plan['plan_id'], 'plan_revision':plan['plan_revision']}}), cfg)
    assert state['approved_snapshot']['dataset_bindings']['dataset']['dataset_id'] == 'package-data'
    assert state['final_response']['status'] == 'plan_approved'
    return len(state['approved_snapshot']['steps'])

print(json.dumps({'wheel':wheel.name, 'source_checkout_imported':False,
    'api_openapi_paths':len(paths), 'mock_graph_execution_steps':asyncio.run(smoke()),
    'removed_agent_packages_in_wheel':False, 'unified_workflow_package_present':True, 'tests_in_wheel':False, 'resources_present':True,
    'role_prompts_present':len(roles), 'production_builders_constructed':True,
    'create_agent_roles':len(roles), 'role_checkpointers_disabled':True}))
