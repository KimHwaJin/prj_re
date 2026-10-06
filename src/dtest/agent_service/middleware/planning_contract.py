"""Select a Skill before enabling planning metadata, within one create_agent loop."""

from dataclasses import replace
from uuid import uuid4

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, SystemMessage

from .discovery import completed_queries
from .prompt_json import response_text, unwrap_json


class PlanningContractMiddleware(AgentMiddleware):
    def __init__(
        self, planning_prompt: str, *, schema, workflow_search_enabled=False
    ):
        self.planning_prompt = planning_prompt
        self.schema = schema
        self.workflow_search_enabled = workflow_search_enabled

    @staticmethod
    def discovered(messages):
        return any(
            name in {"read_skill", "search_tools"}
            for name, _ in completed_queries(messages)
        )

    async def awrap_model_call(self, request, handler):
        if not self.discovered(request.messages):
            # First answer needs no metadata tool binding or full Workflow schema.
            response = await handler(request.override(tools=[]))
            message = response.result[-1]
            if isinstance(message, AIMessage) and message.tool_calls:
                # An unadvertised call must not bypass the explicit selection boundary.
                message = message.model_copy(
                    update={
                        "tool_calls": [],
                        "invalid_tool_calls": [],
                        "additional_kwargs": {
                            key: value
                            for key, value in message.additional_kwargs.items()
                            if key != "tool_calls"
                        },
                        "content": (
                            '{"error":"Return answer or planning with '
                            "registered skill_ids; metadata calls are "
                            "not enabled "
                            'yet."}'
                        ),
                    }
                )
                return replace(
                    response,
                    result=[*response.result[:-1], message],
                    structured_response=None,
                )
            try:
                selected = self.schema.model_validate_json(
                    unwrap_json(response_text(message))
                )
            except ValueError:
                return response  # outer PromptJsonMiddleware owns bounded correction
            if selected.kind != "planning":
                return response
            # The model selected registered Skills; route those lookups through the
            # standard ToolNode. No second classifier model or external execution.
            calls = [
                {
                    "name": "read_skill",
                    "args": {"skill_id": skill},
                    "id": "planning-" + uuid4().hex,
                    "type": "tool_call",
                }
                for skill in selected.skill_ids
            ]
            if (
                self.workflow_search_enabled
                and selected.planning_scope == "end_to_end"
            ):
                calls.append(
                    {
                        "name": "search_workflows",
                        "args": {},
                        "id": "workflow-search-" + uuid4().hex,
                        "type": "tool_call",
                    }
                )
            message = message.model_copy(
                update={"content": "", "tool_calls": calls}
            )
            return replace(
                response,
                result=[*response.result[:-1], message],
                structured_response=None,
            )
        # Derive the contract from invocation messages, never shared mutable state.
        base = request.system_message or SystemMessage(content="")
        content = base.content
        if isinstance(content, str):
            content += (
                "\n\nPlanning contract (metadata has been requested). "
                "Skill selection is finished; return final "
                "answer/plans with skill_ids=[], never planning "
                "again:\n"
            ) + self.planning_prompt
        else:
            content = [
                *content,
                {"type": "text", "text": self.planning_prompt},
            ]
        return await handler(
            request.override(
                system_message=base.model_copy(update={"content": content}),
                tools=[
                    t
                    for t in request.tools
                    if getattr(t, "name", None) != "search_workflows"
                ],
            )
        )
