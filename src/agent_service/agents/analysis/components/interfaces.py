"""Small adapters that keep graph nodes independent from agent internals."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from langgraph.constants import CONFIG_KEY_CHECKPOINTER
from pydantic import BaseModel


OutputT = TypeVar("OutputT", bound=BaseModel)


def _unwrap_json_code_fence(content: str) -> str:
    """Remove a single outer ```json fence without changing its JSON body."""
    stripped = content.strip()
    lines = stripped.splitlines()
    if (
        len(lines) >= 3
        and lines[0].strip().lower() == "```json"
        and lines[-1].strip() == "```"
    ):
        return "\n".join(lines[1:-1]).strip()
    return content


class AsyncInvokableAgent(Protocol):
    """Native async component contract; cancellation must propagate to callers."""
    async def ainvoke(self, payload: Any) -> Any:
        ...


@dataclass
class JsonMessageAgentAdapter:
    """Adapt a LangChain agent graph to the node's asynchronous JSON contract."""

    agent: Any

    async def ainvoke(self, payload: Any) -> Any:
        return await self.agent.ainvoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": json.dumps(
                            payload,
                            ensure_ascii=False,
                            default=str,
                        ),
                    }
                ]
            },
            config={
                "configurable": {
                    # The outer service graph owns persistence. The inner
                    # LLM agent is stateless and must not inherit the outer
                    # graph checkpointer, even for asynchronous execution.
                    CONFIG_KEY_CHECKPOINTER: None,
                }
            },
        )


async def ainvoke_typed(
    agent: AsyncInvokableAgent,
    payload: BaseModel | dict[str, Any],
    output_type: type[OutputT],
) -> OutputT:
    raw_payload = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
    result = await agent.ainvoke(raw_payload)
    if isinstance(result, dict) and "structured_response" in result:
        result = result["structured_response"]
    elif isinstance(result, dict) and result.get("messages"):
        content = getattr(result["messages"][-1], "content", None)
        if isinstance(content, list):
            text_blocks = [
                block.get("text")
                for block in content
                if isinstance(block, dict)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            ]
            content = text_blocks[-1] if text_blocks else None
        if isinstance(content, str) and content.strip():
            return output_type.model_validate_json(
                _unwrap_json_code_fence(content)
            )
    return result if isinstance(result, output_type) else output_type.model_validate(result)


@dataclass
class StructuredLLMAgent:
    """LangChain model adapter with a Pydantic structured output contract."""

    model: Any
    system_prompt: str
    output_type: type[BaseModel]
    method: str = "prompt_json"
    max_validation_attempts: int = 3

    async def ainvoke(self, payload: Any) -> BaseModel:
        from langchain_core.messages import HumanMessage, SystemMessage

        if self.method == "provider_json_schema":
            structured_model = self.model.with_structured_output(
                self.output_type,
                method="json_schema",
            )
            return await structured_model.ainvoke(
                [
                    SystemMessage(content=self.system_prompt),
                    HumanMessage(
                        content=json.dumps(
                            payload,
                            ensure_ascii=False,
                            default=str,
                        )
                    ),
                ]
            )
        if self.method != "prompt_json":
            raise ValueError(f"Unsupported structured output method: {self.method!r}")

        schema = json.dumps(
            self.output_type.model_json_schema(),
            ensure_ascii=False,
        )
        system_prompt = (
            f"{self.system_prompt}\n\n"
            "Return exactly one JSON object matching this JSON Schema. "
            "Include every required field and do not return markdown or "
            f"explanatory text.\nJSON Schema: {schema}"
        )
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(
                content=json.dumps(payload, ensure_ascii=False, default=str)
            ),
        ]
        last_error: Exception | None = None
        for _ in range(self.max_validation_attempts):
            response = await self.model.ainvoke(messages)
            content = getattr(response, "content", response)
            if not isinstance(content, str) or not content.strip():
                last_error = ValueError(
                    "Structured LLM response must contain JSON text"
                )
            else:
                try:
                    return self.output_type.model_validate_json(
                        _unwrap_json_code_fence(content)
                    )
                except ValueError as exc:
                    last_error = exc
            messages.extend(
                [
                    response,
                    HumanMessage(
                        content=(
                            "The previous response failed JSON/Pydantic validation. "
                            "Return the complete corrected JSON object only. "
                            f"Validation error: {last_error}"
                        )
                    ),
                ]
            )
        raise ValueError(
            "Structured LLM response failed validation after "
            f"{self.max_validation_attempts} attempts: {last_error}"
        ) from last_error


@dataclass
class LabelOnlyLLMAgent:
    """Classify with plain text and build the typed result in Python."""

    model: Any
    system_prompt: str
    output_type: type[BaseModel]
    label_field: str
    allowed_labels: tuple[str, ...]

    async def ainvoke(self, payload: Any) -> BaseModel:
        from langchain_core.messages import HumanMessage, SystemMessage

        labels = "\n".join(self.allowed_labels)
        prompt = (
            f"{self.system_prompt}\n\n"
            "Classify the request and return exactly one label from the list "
            "below. Return only the label, with no JSON, markdown, reason, or "
            f"other text.\n{labels}"
        )
        response = await self.model.ainvoke(
            [
                SystemMessage(content=prompt),
                HumanMessage(
                    content=json.dumps(payload, ensure_ascii=False, default=str)
                ),
            ]
        )
        raw_label = response.content if hasattr(response, "content") else response
        if not isinstance(raw_label, str):
            raise ValueError("LLM label response must be plain text")
        label = raw_label.strip().strip("`\"'").strip()
        if label not in self.allowed_labels:
            raise ValueError(
                f"Unexpected LLM label for {self.label_field}: {raw_label!r}"
            )
        return self.output_type.model_validate(
            {
                self.label_field: label,
                "reason": f"LLM classified the request as {label}.",
            }
        )


@dataclass
class SimpleLLMAgent:
    """Invoke a chat model directly for plain text question answering."""

    model: Any
    system_prompt: str

    async def ainvoke(self, payload: Any) -> dict[str, str]:
        from langchain_core.messages import HumanMessage, SystemMessage

        if not isinstance(payload, dict):
            raise TypeError("SimpleLLMAgent payload must be a dictionary")
        user_request = str(payload.get("user_request") or "").strip()
        if not user_request:
            raise ValueError("user_request is required")

        response = await self.model.ainvoke(
            [
                SystemMessage(content=self.system_prompt),
                HumanMessage(content=user_request),
            ]
        )
        answer = response.content if hasattr(response, "content") else response
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("LLM response must contain a non-empty text answer")
        return {"answer": answer.strip()}


@dataclass
class PlaceholderAgent:
    message: str

    async def ainvoke(self, payload: Any) -> dict[str, str]:
        return {"status": "placeholder", "message": self.message}
