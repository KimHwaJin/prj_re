"""Declare conditional execution decisions; no LLM tools are exposed."""
from typing import Any

from ...components.interfaces import StructuredLLMAgent
from ...schemas.agents.orchestration_schema import ConditionalDecisionOutput
from .._prompts import load_prompt


def build_agent(model: Any, *, structured_output_mode: str = "prompt_json") -> StructuredLLMAgent:
    return StructuredLLMAgent(
        model=model,
        system_prompt=load_prompt(__package__),
        output_type=ConditionalDecisionOutput,
        method=structured_output_mode,
    )
