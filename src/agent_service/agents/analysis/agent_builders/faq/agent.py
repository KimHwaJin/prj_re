"""Declare FAQ answers; no LLM tools are exposed."""
from typing import Any

from ...components.interfaces import SimpleLLMAgent
from .._prompts import load_prompt


def build_agent(model: Any) -> SimpleLLMAgent:
    return SimpleLLMAgent(model=model, system_prompt=load_prompt(__package__))
