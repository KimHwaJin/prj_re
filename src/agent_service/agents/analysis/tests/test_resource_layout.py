"""Behavioral checks for installed resources and saved Workflow path migration."""
from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent_service.agents.analysis.agent_builders.report_writer import build_agent
from agent_service.agents.analysis.dependencies import create_llm_dependencies
from agent_service.agents.analysis.graph import build_analysis_workflow_graph
from agent_service.agents.analysis.resource_paths import (
    RELOCATED_TOOL_PREFIX, TOOL_SOURCE_PREFIX, SOURCE_ROOT, TOOLS_ROOT,
    SKILLS_ROOT, SKILL_INDEX_PATH, TOOL_REGISTRY_PATH, resolve_tool_source,
)
from agent_service.agents.analysis.tools.catalog import _resolve_skill_document
from agent_service.agents.analysis.workflow.rule_based_notebook_generator import build_notebook
from agent_service.agents.analysis.tests.test_service_load_mock import mock_settings, action
from app.workflow.skills.generate_skill_index import build_index
from app.workflow.tools.generate_tool_registry import build_registry


def test_catalog_generators_and_shipped_resources_agree():
    assert build_index(SKILLS_ROOT) == yaml.safe_load(SKILL_INDEX_PATH.read_text())
    assert build_registry(TOOLS_ROOT) == yaml.safe_load(TOOL_REGISTRY_PATH.read_text())
    for entry in yaml.safe_load(TOOL_REGISTRY_PATH.read_text())['tools'].values():
        assert resolve_tool_source(TOOL_SOURCE_PREFIX + entry['source'], SOURCE_ROOT).is_file()
    assert build_agent(object()).system_prompt.strip()


@pytest.mark.parametrize('prefix', [RELOCATED_TOOL_PREFIX, TOOL_SOURCE_PREFIX])
def test_saved_tool_paths_resolve_to_original_workflow_package(prefix, tmp_path):
    assert resolve_tool_source(prefix + 'eda/profile_data.py', tmp_path) == TOOLS_ROOT / 'eda/profile_data.py'
    with pytest.raises(ValueError, match='outside resource root'):
        resolve_tool_source(prefix + '../catalogs/tool_registry.yaml', tmp_path)


def test_legacy_and_current_skill_names_resolve_to_same_document():
    old = _resolve_skill_document('app/workflow/skills/eda/data_quality_check.md')
    new = _resolve_skill_document('agent_service/agents/analysis/resources/skills/eda/data_quality_check.md')
    assert old == new


@pytest.mark.asyncio
async def test_approval_resume_uses_saved_relocated_workflow_paths(tmp_path):
    settings = mock_settings(EXECUTOR_SOURCE_TYPE='INLINE')
    saver = InMemorySaver()
    graph = build_analysis_workflow_graph(create_llm_dependencies(settings), settings, checkpointer=saver)
    session = str(uuid4())
    config = {'configurable': {'thread_id': session}}
    state = await graph.ainvoke({'user_request': 'layout compatibility', 'session_id': session,
        'user_id': str(uuid4()), 'project_id': str(uuid4())}, config)
    for response in ['mock', {'objective': 'EDA'}, {'candidate_number': 1}]:
        state = await graph.ainvoke(Command(resume=response), config)
    assert action(state) == 'workflow_approval'
    current = state['workflow']
    serialized = json.dumps(current)
    assert TOOL_SOURCE_PREFIX in serialized
    assert RELOCATED_TOOL_PREFIX not in serialized
    relocated = json.loads(json.dumps(deepcopy(current)).replace(TOOL_SOURCE_PREFIX, RELOCATED_TOOL_PREFIX))
    assert relocated != current
    assert build_notebook(relocated, project_root=tmp_path)['cells'] == build_notebook(current, project_root=tmp_path)['cells']
    await graph.aupdate_state(config, {'workflow': relocated})
    # Rebuild the graph while retaining its checkpoint; approval must compile
    # saved transient paths through the restored app/workflow package.
    rebuilt = build_analysis_workflow_graph(create_llm_dependencies(settings), settings, checkpointer=saver)
    result = await rebuilt.ainvoke(Command(resume={'approved': True}), config)
    assert result['notebook']['cells']
    assert result['execution_steps']
    assert result['executor_submit_response']['skipped']
