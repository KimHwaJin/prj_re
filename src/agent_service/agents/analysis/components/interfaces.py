"""Graph-facing async contract and schema validation; no direct model calls."""
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar
from pydantic import BaseModel
from agent_service.context import AgentContext
from agent_service.middleware.prompt_json import unwrap_json

OutputT = TypeVar("OutputT", bound=BaseModel)

class AsyncInvokableAgent(Protocol):
    async def ainvoke(self, payload: Any, *, context: AgentContext | None = None) -> Any: ...

async def ainvoke_typed(
    agent: AsyncInvokableAgent,
    payload: BaseModel | dict[str, Any],
    output_type: type[OutputT],
    *,
    context: AgentContext | None = None,
) -> OutputT:
    raw_payload = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
    result = await agent.ainvoke(raw_payload, context=context)
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
                unwrap_json(content)
            )
    return result if isinstance(result, output_type) else output_type.model_validate(result)


@dataclass
class PlaceholderAgent:
    message: str
    async def ainvoke(self, payload: Any, *, context: AgentContext | None = None) -> dict[str, str]:
        return {"status": "placeholder", "message": self.message}
