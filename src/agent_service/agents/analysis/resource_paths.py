"""Installed analysis resources and compatibility for persisted Tool paths.

Resources ship with the package; generated artifacts belong on the configured
PV. A renamed source directory must not invalidate a paused Workflow document.
"""
from importlib.resources import files
from pathlib import Path

PACKAGE_ROOT = Path(str(files('agent_service.agents.analysis'))).resolve()
SOURCE_ROOT = Path(str(files('agent_service'))).resolve().parent
RESOURCES_ROOT = PACKAGE_ROOT / 'resources'
SKILLS_ROOT = RESOURCES_ROOT / 'skills'
TOOLS_ROOT = RESOURCES_ROOT / 'executor_tools'
CATALOGS_ROOT = RESOURCES_ROOT / 'catalogs'
SKILL_INDEX_PATH = CATALOGS_ROOT / 'skill_index.yaml'
TOOL_REGISTRY_PATH = CATALOGS_ROOT / 'tool_registry.yaml'
TOOL_SOURCE_PREFIX = 'agent_service/agents/analysis/resources/executor_tools/'
SKILL_SOURCE_PREFIX = 'agent_service/agents/analysis/resources/skills/'
LEGACY_TOOL_PREFIX = 'app/workflow/tools/'
LEGACY_SKILL_PREFIX = 'app/workflow/skills/'


def resolve_tool_source(source: str, project_root: Path) -> Path:
    """Resolve packaged old/new paths, retaining explicit local test sources.

    Both aliases stay inside the shipped Tool directory, even with traversal or
    symlinks. Arbitrary local sources keep the existing project-root restriction.
    This is path compatibility, not a promise of historical Tool version support.
    """
    for prefix in (LEGACY_TOOL_PREFIX, TOOL_SOURCE_PREFIX):
        if source.startswith(prefix):
            candidate = (TOOLS_ROOT / source[len(prefix):]).resolve()
            if not candidate.is_relative_to(TOOLS_ROOT.resolve()):
                raise ValueError(f'tool source is outside resource root: {source}')
            return candidate
    root = project_root.resolve()
    candidate = (root / source).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError(f'tool source is outside project root: {source}')
    return candidate
