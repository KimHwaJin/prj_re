"""Bounded JSON validation for providers without native JSON Schema support."""

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel


class StructuredResponseError(ValueError):
    """The model returned unusable content after bounded validation attempts."""


def unwrap_json(content: str) -> str:
    lines = content.strip().splitlines()
    if (
        len(lines) >= 3
        and lines[0].strip().lower() == "```json"
        and lines[-1].strip() == "```"
    ):
        return "\n".join(lines[1:-1]).strip()
    return content


def response_text(message) -> str:
    content = message.content
    if isinstance(content, list):
        content = "".join(
            block["text"]
            for block in content
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        )
    if not isinstance(content, str) or not content.strip():
        raise ValueError("LLM response must contain non-empty text")
    return content


class PromptJsonMiddleware(AgentMiddleware):
    def __init__(
        self,
        schema: type[BaseModel],
        *,
        max_attempts: int = 3,
        validate_response=None,
    ):
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self.schema, self.max_attempts = schema, max_attempts
        self.validate_response = validate_response

    async def awrap_model_call(self, request, handler):
        current = request
        for attempt in range(self.max_attempts):
            # Model/network/cancellation failures propagate; only invalid JSON
            # is retried here. SDK network retry settings remain authoritative.
            response = await handler(current)
            message = response.result[-1]
            if isinstance(message, AIMessage) and message.tool_calls:
                return response  # let create_agent execute explicitly enabled tools
            try:
                parsed = self.schema.model_validate_json(
                    unwrap_json(response_text(message))
                )
                if self.validate_response is not None:
                    self.validate_response(parsed, request)
                return response
            except ValueError as exc:
                if attempt + 1 == self.max_attempts:
                    raise StructuredResponseError(
                        f"Structured LLM response failed validation after {self.max_attempts} attempts: {exc}"
                    ) from exc
                current = request.override(
                    messages=[
                        *current.messages,
                        message,
                        HumanMessage(
                            content=(
                                "The previous response failed "
                                "JSON/Pydantic validation. Return the "
                                "complete corrected JSON object only. "
                                "Validation error: "
                            )
                            + str(exc)
                        ),
                    ]
                )
        raise AssertionError("unreachable")
