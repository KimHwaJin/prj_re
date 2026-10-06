"""Append project instructions afresh on every model call, including retries."""
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage
from dtest.agent_service.context import AgentContext


class ProjectPromptMiddleware(AgentMiddleware):
    async def awrap_model_call(self, request, handler):
        context: AgentContext | None = request.runtime.context
        prompt = context.project_system_prompt if context else ""
        if not prompt:
            return await handler(request)
        base = request.system_message
        if base is None:
            message = SystemMessage(content=prompt)
        elif isinstance(base.content, str):
            message = base.model_copy(update={"content": base.content + "\n\n# Project instructions\n" + prompt})
        else:
            message = base.model_copy(update={"content": [*base.content, {"type": "text", "text": "\n\n# Project instructions\n" + prompt}]})
        return await handler(request.override(system_message=message))
