"""Only registered production assets are advertised by the deployed catalog."""

import yaml

from dtest.agent_service.agents.analysis.planning.catalog import AssetCatalog
from dtest.agent_service.agents.analysis.workflow.paths import (
    SKILL_INDEX_PATH,
    TOOL_REGISTRY_PATH,
)


def test_shipped_catalog_advertises_registered_ready_tools_only():
    registry = yaml.safe_load(TOOL_REGISTRY_PATH.read_text())["tools"]
    catalog = AssetCatalog()
    assert set(catalog.metadata["tools"]) == {
        key
        for key, entry in registry.items()
        if entry.get("availability", "ready") == "ready"
    }
    assert set(catalog.sources) == set(catalog.metadata["tools"])


def test_shipped_skills_reference_only_available_registered_tools():
    catalog = AssetCatalog()
    index = yaml.safe_load(SKILL_INDEX_PATH.read_text())["skills"]
    assert set(catalog.metadata["skills"]) == set(index)
    for skill in catalog.public_skills():
        assert set(skill["tools"]) <= catalog.metadata["tools"].keys()
        assert "code" not in skill
