"""Canonical Workflow asset paths and compatibility for persisted source names.

app/workflow is the authoring and distribution package. Transient resource
paths from refactors 005-008 remain readable, without duplicate asset copies.
"""
from importlib.resources import files
from pathlib import Path

WORKFLOW_ROOT = Path(str(files("app.workflow"))).resolve()
SOURCE_ROOT = WORKFLOW_ROOT.parent.parent
SKILLS_ROOT = WORKFLOW_ROOT / "skills"
TOOLS_ROOT = WORKFLOW_ROOT / "tools"
SKILL_INDEX_PATH = SKILLS_ROOT / "skill_index.yaml"
TOOL_REGISTRY_PATH = TOOLS_ROOT / "tool_registry.yaml"
TOOL_SOURCE_PREFIX = "app/workflow/tools/"
SKILL_SOURCE_PREFIX = "app/workflow/skills/"
RELOCATED_TOOL_PREFIX = "agent_service/agents/analysis/resources/executor_tools/"
RELOCATED_SKILL_PREFIX = "agent_service/agents/analysis/resources/skills/"


def resolve_tool_source(source: str, project_root: Path) -> Path:
    """Resolve both persisted prefixes to the single maintained Tool package.

    This preserves path lookup, not historical Tool contents or versions.
    Explicit local sources still stay inside their supplied project root.
    """
    for prefix in (TOOL_SOURCE_PREFIX, RELOCATED_TOOL_PREFIX):
        if source.startswith(prefix):
            candidate = (TOOLS_ROOT / source[len(prefix):]).resolve()
            if not candidate.is_relative_to(TOOLS_ROOT.resolve()):
                raise ValueError(f"tool source is outside resource root: {source}")
            return candidate
    root = project_root.resolve()
    candidate = (root / source).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError(f"tool source is outside project root: {source}")
    return candidate
