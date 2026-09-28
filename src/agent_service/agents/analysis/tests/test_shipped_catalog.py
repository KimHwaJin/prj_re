"""Contract for the reduced, shipped catalog (ee66fac and later)."""
import importlib.util

import pytest

from agent_service.agents.analysis.tools.catalog import (
    _load_skill_index_document, _load_tool_registry_document, validate_skill_names,
)


@pytest.mark.parametrize("name", ["select_features", "split_dataset"])
def test_retired_preprocessing_tools_are_not_advertised(name):
    assert name not in _load_tool_registry_document()["tools"]
    assert importlib.util.find_spec(
        f"agent_service.agents.analysis.workflow.tools.preprocessing.{name}"
    ) is None


def test_shipped_skill_catalog_is_not_extended_by_test_fixtures():
    assert set(_load_skill_index_document()["skills"]) == {
        "data_load", "data_quality_check", "dataset_preparation",
    }
    with pytest.raises(ValueError, match="Skill Index"):
        validate_skill_names(["test_conditional"])
