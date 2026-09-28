"""Test-only conditional Skills using shipped Tool implementations.

The deployed catalog deliberately has no adaptive Skill. Patch only these
unit-test cases; never register synthetic Skills in production assets.
"""
from contextlib import contextmanager, ExitStack
from copy import deepcopy
from unittest.mock import patch

from agent_service.agents.analysis.tools import catalog
from agent_service.agents.analysis.workflow import adaptive_workflow, workflow_compiler


@contextmanager
def conditional_catalog():
    index = deepcopy(catalog._load_skill_index_document())
    source = index["skills"]["data_quality_check"]["source"]
    index["skills"]["test_conditional"] = {
        "source": source,
        "tools": [{"tool": "detect_outliers", "execution": "conditional",
                   "condition_tool": "data_quality_check.profile_data",
                   "condition": "Inspect outliers when the profile warrants it."}],
    }
    index["skills"]["test_eda"] = {
        "source": source,
        "tools": [
            {"tool": "compute_statistics", "execution": "always", "condition_tool": "없음"},
            {"tool": "profile_data", "execution": "conditional", "condition_tool": "compute_statistics", "condition": "Inspect profile."},
            {"tool": "detect_outliers", "execution": "conditional", "condition_tool": "compute_statistics", "condition": "Inspect outliers."},
        ],
    }
    with ExitStack() as stack:
        for module in (catalog, workflow_compiler, adaptive_workflow):
            stack.enter_context(patch.object(module, "_load_skill_index_document", return_value=index))
        yield
