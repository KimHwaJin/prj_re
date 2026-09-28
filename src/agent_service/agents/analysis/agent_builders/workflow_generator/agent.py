"""LangChain Workflow Generator agent construction."""

from __future__ import annotations

import json
from typing import Any

from langchain.agents import create_agent
from ...components.interfaces import JsonMessageAgentAdapter
from ...schemas.workflows.workflow_plan_format import WorkflowPlanOutput
from .._prompts import load_prompt


def build_agent(
    model: Any,
    *,
    structured_output_mode: str = "prompt_json",
):
    """Build the Workflow Agent; Skill documents are supplied by its graph node.

    No catalog tool is exposed to the LLM. Middleware is added in the next
    runtime-contract step, without changing the output contract here.
    """
    system_prompt = load_prompt(__package__)
    agent_kwargs = {
        "model": model,
        "tools": [],
        "system_prompt": system_prompt,
        "name": "workflow_generator_agent",
    }
    schema = WorkflowPlanOutput.model_json_schema()
    agent_kwargs["system_prompt"] = (
        f"{system_prompt}\n\n"
        "Using the provided workflow_resources, return exactly one "
        "JSON object matching the following JSON Schema. Do not return "
        "markdown or explanatory text.\n"
        f"JSON Schema: {json.dumps(schema, ensure_ascii=False)}"
    )
    if structured_output_mode == "provider_json_schema":
        agent_kwargs["model"] = model.bind(
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "strict": True,
                    "name": WorkflowPlanOutput.__name__,
                    "schema": schema,
                },
            }
        )
    elif structured_output_mode != "prompt_json":
        raise ValueError(
            f"Unsupported workflow structured output mode: {structured_output_mode!r}"
        )
    return JsonMessageAgentAdapter(create_agent(**agent_kwargs))


__all__ = ["build_agent"]
