"""Installed roots for the single maintained Skill/Tool asset package."""
from importlib.resources import files
from pathlib import Path

WORKFLOW_ROOT = Path(str(files("dtest.agent_service.agents.analysis.workflow"))).resolve()
SKILLS_ROOT = WORKFLOW_ROOT / "skills"
TOOLS_ROOT = WORKFLOW_ROOT / "tools"
SKILL_INDEX_PATH = SKILLS_ROOT / "skill_index.yaml"
TOOL_REGISTRY_PATH = TOOLS_ROOT / "tool_registry.yaml"
