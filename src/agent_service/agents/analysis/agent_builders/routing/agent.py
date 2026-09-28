"""Declare the routing Agent; no model or pool is created at import time.

No LLM tools are exposed. The existing label response contract is retained
until the shared create_agent/middleware migration.
"""
from typing import Any

from ...components.interfaces import LabelOnlyLLMAgent
from ...schemas.agents.orchestration_schema import RoutingOutput
from .._prompts import load_prompt


def build_agent(model: Any) -> LabelOnlyLLMAgent:
    return LabelOnlyLLMAgent(
        model=model,
        system_prompt=load_prompt(__package__),
        output_type=RoutingOutput,
        label_field='route',
        allowed_labels=('analysis', 'faq', 'file_lookup', 'revise_workflow', 'reselect_data', 'cancel'),
    )
