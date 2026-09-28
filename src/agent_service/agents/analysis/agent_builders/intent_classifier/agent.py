"""Declare the intent_classifier Agent; no model or pool is created at import time.

No LLM tools are exposed. The existing label response contract is retained
until the shared create_agent/middleware migration.
"""
from typing import Any

from ...components.interfaces import LabelOnlyLLMAgent
from ...schemas.agents.orchestration_schema import AnalysisIntentOutput
from .._prompts import load_prompt


def build_agent(model: Any) -> LabelOnlyLLMAgent:
    return LabelOnlyLLMAgent(
        model=model,
        system_prompt=load_prompt(__package__),
        output_type=AnalysisIntentOutput,
        label_field='intent',
        allowed_labels=('failure_prediction', 'root_cause', 'data_drift'),
    )
