"""Generated registries agree with the deployed, canonical Skill/Tool assets."""
import yaml

from dtest.agent_service.agents.analysis.agent_builders.execution_report.agent import build_agent
from dtest.agent_service.agents.analysis.planning.catalog import AssetCatalog
from dtest.agent_service.agents.analysis.workflow.paths import (
    WORKFLOW_ROOT, TOOLS_ROOT, SKILLS_ROOT, SKILL_INDEX_PATH, TOOL_REGISTRY_PATH,
)
from dtest.agent_service.agents.analysis.workflow.skills.generate_skill_index import build_index
from dtest.agent_service.agents.analysis.workflow.tools.generate_tool_registry import build_registry


def test_catalog_generators_and_shipped_resources_agree():
    assert build_index(SKILLS_ROOT) == yaml.safe_load(SKILL_INDEX_PATH.read_text())
    assert build_registry(TOOLS_ROOT) == yaml.safe_load(TOOL_REGISTRY_PATH.read_text())
    catalog = AssetCatalog()
    for key, entry in yaml.safe_load(TOOL_REGISTRY_PATH.read_text())["tools"].items():
        source = (TOOLS_ROOT / entry["source"]).resolve()
        assert source.is_relative_to(TOOLS_ROOT) and source.is_file()
        assert (key in catalog.sources) == (entry.get("availability", "ready") == "ready")
    assert catalog.root == WORKFLOW_ROOT
    assert build_agent(object()).agent.checkpointer is False
