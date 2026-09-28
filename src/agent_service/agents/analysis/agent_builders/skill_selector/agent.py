"""Declare Skill selection. Catalog reads are application-controlled, not LLM tools."""
from typing import Any

from ...components.interfaces import StructuredLLMAgent
from ...schemas.agents.workflow_generator_schema import SkillSelectionOutput
from ...tools.catalog import load_workflow_catalog_context
from .._prompts import load_prompt


def build_agent(model: Any, *, structured_output_mode: str = "prompt_json") -> StructuredLLMAgent:
    return StructuredLLMAgent(
        model=model,
        system_prompt=f"{load_prompt(__package__)}\n\n{load_workflow_catalog_context()}",
        output_type=SkillSelectionOutput,
        method=structured_output_mode,
    )
