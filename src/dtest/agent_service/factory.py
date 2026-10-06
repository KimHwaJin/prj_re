"""Shared create_agent construction and small domain-response conversion helpers."""
from dataclasses import dataclass, replace
from typing import Any, Callable, Sequence
import json

from langchain.agents import create_agent
from langchain.agents.structured_output import ProviderStrategy, StructuredOutputValidationError
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel

from dtest.agent_service.context import AgentContext
from dtest.agent_service.middleware import PromptJsonMiddleware
from dtest.agent_service.middleware.prompt_json import response_text, unwrap_json


def last_text(result: dict) -> str:
    return response_text(result["messages"][-1])


def json_output(schema: type[BaseModel]):
    def decode(result):
        structured = result.get("structured_response")
        if structured is not None:
            return structured if isinstance(structured, schema) else schema.model_validate(structured)
        return schema.model_validate_json(unwrap_json(last_text(result)))
    return decode


def text_output(key: str):
    return lambda result: {key: last_text(result).strip()}


@dataclass(frozen=True)
class RoleAgent:
    """Translate node payload/result only; all model calls run inside create_agent."""
    agent: Any
    decode: Callable = lambda result: result
    input_key: str | None = None
    model_name: str = ""

    async def ainvoke(self, payload: Any, *, context: AgentContext | None = None):
        if self.input_key:
            if not isinstance(payload, dict):
                raise TypeError("Text Agent payload must be a dictionary")
            content = str(payload.get(self.input_key) or "").strip()
            if not content:
                raise ValueError(f"{self.input_key} is required")
        else:
            content = json.dumps(payload, ensure_ascii=False, default=str)
        try:
            result = await self.agent.ainvoke(
                {"messages": [{"role": "user", "content": content}]},
                config={'recursion_limit': (context or AgentContext()).recursion_limit},
                context=replace(context or AgentContext(), model_name=self.model_name),
                # LangGraph 1.2.11 otherwise inherits outer sync durability and
                # accesses a missing checkpoint future in this stateless graph.
                # Use its public API; the version's no-checkpointer warning is
                # expected. The outer graph still keeps its own sync durability.
                durability="async",
            )
        except StructuredOutputValidationError as exc:
            # Keep the graph node's existing bounded validation retry contract.
            # Transport errors and cancellation are deliberately not translated.
            raise ValueError(str(exc)) from exc
        return self.decode(result)


def build_role_agent(
    model: BaseChatModel,
    *,
    name: str,
    system_prompt: str,
    tools: Sequence,
    middleware: Sequence[AgentMiddleware],
    decode: Callable = lambda result: result,
    input_key: str | None = None,
    output_type: type[BaseModel] | None = None,
    structured_output_mode: str = "prompt_json",
    max_validation_attempts: int = 3,
    validate_response: Callable | None = None,
    store=None,
) -> RoleAgent:
    policies = list(middleware)
    from dtest.agent_service.middleware.project_memory import ProjectMemoryMiddleware
    if not any(isinstance(policy, ProjectMemoryMiddleware) for policy in policies):
        policies.append(ProjectMemoryMiddleware(role=name))
    kwargs = {}
    if output_type is not None:
        system_prompt += (
            "\n\nReturn exactly one JSON object matching this JSON Schema. "
            "Include every required field and do not return markdown or "
            "explanatory text.\nJSON Schema: "
            + json.dumps(output_type.model_json_schema(), ensure_ascii=False)
        )
        if structured_output_mode == "provider_json_schema":
            kwargs["response_format"] = ProviderStrategy(output_type, strict=True)
            if validate_response is not None:
                policies.insert(0, PromptJsonMiddleware(output_type,
                    max_attempts=max_validation_attempts, validate_response=validate_response))
        elif structured_output_mode == "prompt_json":
            # Outer validation re-enters every supplied model policy on retry.
            policies.insert(
                0, PromptJsonMiddleware(output_type, max_attempts=max_validation_attempts,
                                        validate_response=validate_response)
            )
        else:
            raise ValueError(f"Unsupported structured output mode: {structured_output_mode!r}")
    agent = create_agent(
        model=model,
        tools=list(tools),
        system_prompt=system_prompt,
        name=name,
        context_schema=AgentContext,
        middleware=policies,
        checkpointer=False,
        store=store,
        **kwargs,
    )
    return RoleAgent(
        agent=agent, decode=decode, input_key=input_key,
        model_name=getattr(model, "model_name", ""),
    )
