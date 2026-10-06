"""Bound metadata exploration; repeated lookups must not grow the prompt forever."""

import json

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, ToolMessage, SystemMessage


def completed_queries(messages):
    answered = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
    return [
        (
            call["name"],
            json.dumps(call["args"], sort_keys=True, ensure_ascii=False),
        )
        for message in messages
        if isinstance(message, AIMessage)
        for call in message.tool_calls
        if call["id"] in answered
    ]


class MetadataDiscoveryMiddleware(AgentMiddleware):
    def __init__(self, *, max_rounds=4, final_instruction=None):
        self.max_rounds = max_rounds
        self.final_instruction = final_instruction or (
            "Return the final valid Reply JSON with plans or an "
            "honest answer/clarification "
            "now."
        )

    async def awrap_model_call(self, request, handler):
        queries = completed_queries(request.messages)
        rounds = sum(
            isinstance(m, AIMessage) and bool(m.tool_calls)
            for m in request.messages
        )
        # A duplicate means discovery is looping; available observations are
        # already in the conversation. Force synthesis instead of more I/O.
        finish = rounds >= self.max_rounds or len(queries) != len(set(queries))
        if not finish:
            return await handler(request)
        base = request.system_message or SystemMessage(content="")
        note = (
            "\n\nMetadata discovery is complete. Use the Skill/Tool "
            "observations already returned. Do not request tools "
            "again. "
        ) + self.final_instruction
        content = (
            base.content + note
            if isinstance(base.content, str)
            else [*base.content, {"type": "text", "text": note}]
        )
        response = await handler(
            request.override(
                tools=[],
                system_message=base.model_copy(update={"content": content}),
            )
        )
        if any(
            isinstance(m, AIMessage) and m.tool_calls for m in response.result
        ):
            raise ValueError(
                "Model requested metadata tools after the discovery limit"
            )
        return response

    async def awrap_tool_call(self, request, handler):
        key = (
            request.tool_call["name"],
            json.dumps(
                request.tool_call["args"], sort_keys=True, ensure_ascii=False
            ),
        )
        if key in completed_queries(request.state["messages"]):
            return ToolMessage(
                content=(
                    "This exact lookup was already returned. Refer to "
                    "the earlier observation and produce the final JSON "
                    "for your assigned response "
                    "schema."
                ),
                tool_call_id=request.tool_call["id"],
                name=request.tool_call["name"],
            )
        return await handler(request)
