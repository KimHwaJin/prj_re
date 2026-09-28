"""Canonical Workflow asset paths and aliases for persisted source names."""
from importlib.resources import files
from pathlib import Path

WORKFLOW_ROOT = Path(str(files("agent_service.agents.analysis.workflow"))).resolve()
SOURCE_ROOT = WORKFLOW_ROOT.parents[3]
SKILLS_ROOT = WORKFLOW_ROOT / "skills"
TOOLS_ROOT = WORKFLOW_ROOT / "tools"
SKILL_INDEX_PATH = SKILLS_ROOT / "skill_index.yaml"
TOOL_REGISTRY_PATH = TOOLS_ROOT / "tool_registry.yaml"
TOOL_SOURCE_PREFIX = "agent_service/agents/analysis/workflow/tools/"
SKILL_SOURCE_PREFIX = "agent_service/agents/analysis/workflow/skills/"
LEGACY_TOOL_PREFIX = "app/workflow/tools/"
LEGACY_SKILL_PREFIX = "app/workflow/skills/"
RELOCATED_TOOL_PREFIX = "agent_service/agents/analysis/resources/executor_tools/"
RELOCATED_SKILL_PREFIX = "agent_service/agents/analysis/resources/skills/"
TOOL_SOURCE_PREFIXES = (TOOL_SOURCE_PREFIX, LEGACY_TOOL_PREFIX, RELOCATED_TOOL_PREFIX)
SKILL_SOURCE_PREFIXES = (SKILL_SOURCE_PREFIX, LEGACY_SKILL_PREFIX, RELOCATED_SKILL_PREFIX)


def resolve_tool_source(source: str, project_root: Path) -> Path:
    """Resolve saved aliases to the single maintained Tool package.

    This preserves path lookup, not historical Tool contents or versions.
    Explicit local sources still stay inside their supplied project root.
    """
    for prefix in TOOL_SOURCE_PREFIXES:
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
