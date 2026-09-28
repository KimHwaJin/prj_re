"""Request receipt and top-level Routing Agent node."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from langchain_core.runnables import RunnableConfig

from agent_config import (
    LOCAL_MOCK_PROJECT_ID,
    LOCAL_MOCK_SESSION_ID,
    LOCAL_MOCK_USER_ID,
    build_langgraph_thread_id,
)
from agent_service.agents.analysis.components.interfaces import invoke_typed
from agent_service.agents.analysis.dependencies import AgentDependencies
from agent_service.agents.analysis.state import AnalysisWorkflowState
from agent_service.agents.analysis.schemas.agents.orchestration_schema import RequestContext, RoutingOutput

ALLOWED_ROUTES = {
    "main": {
        "analysis",
        "faq",
        "file_lookup",
        "cancel",
    },
    "workflow_rejected": {
        "analysis",
        "faq",
        "file_lookup",
        "revise_workflow",
        "reselect_data",
        "cancel",
    },
}

def _message_content(message: Any) -> str:
    """Extract plain user text from Agent Chat message representations."""
    content = (
        message.get("content")
        if isinstance(message, dict)
        else getattr(message, "content", "")
    )
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(parts).strip()
    return ""


def receive_request(
    state: AnalysisWorkflowState,
    config: RunnableConfig,
) -> dict:
    request = (state.get("user_request") or "").strip()
    request_from_messages = False
    if not request:
        messages = state.get("messages") or []
        if messages:
            request = _message_content(messages[-1])
            request_from_messages = bool(request)
    if not request:
        raise ValueError("user_request is required")

    configurable = (config or {}).get("configurable") or {}
    runtime_thread_id = configurable.get("thread_id")
    session_id = (
        state.get("session_id")
        or runtime_thread_id
        or LOCAL_MOCK_SESSION_ID
    )
    context = RequestContext.model_validate(
        {
            "user_id": state.get("user_id") or LOCAL_MOCK_USER_ID,
            "project_id": state.get("project_id") or LOCAL_MOCK_PROJECT_ID,
            "session_id": session_id,
        }
    )
    context_payload = context.model_dump(mode="json")
    thread_id = state.get("thread_id") or build_langgraph_thread_id(
        context.session_id
    )

    # TODO(CRUD): 사용자 메시지 POST
    return {
        **context_payload,
        "user_request": request,
        "request_id": state.get("request_id") or str(uuid4()),
        "thread_id": thread_id,
        "routing_context": "main",
        "additional_information": state.get("additional_information") or {},
        "workflow_revision": state.get("workflow_revision", 0),
        "return_to": "main_conversation",
        "action_query": None,
        # Agent Chat already placed the user message in state. Avoid adding it
        # twice; direct API callers still receive the normalized message.
        "messages": (
            []
            if request_from_messages
            else [{"role": "user", "content": request}]
        ),
    }


def ensure_analysis_task(state: AnalysisWorkflowState) -> dict:
    """Create one task id per analysis while keeping the conversation thread."""
    return {"task_id": str(uuid4())}


def make_route_user_request(deps: AgentDependencies):
    """Create a node that classifies the user's top-level route."""

    def route_user_request(state: AnalysisWorkflowState) -> dict:
        output = invoke_typed(
            deps.routing_agent,
            {
                "user_request": state["user_request"],
                "routing_context": state["routing_context"],
                "approval_feedback": state.get("approval_feedback"),            
            },    
            RoutingOutput,
        )
        
        if output.route not in ALLOWED_ROUTES[state["routing_context"]]:
            raise ValueError(
                f"{state['routing_context']}에서 허용되지 않는 route입니다: "
                f"{output.route}"
            )
        # TODO(CRUD): 에이전트 메시지 POST
        payload = output.model_dump(mode="json")
        return {
            "routing_result": payload,
        }

    return route_user_request


__all__ = ["ensure_analysis_task", "make_route_user_request", "receive_request"]
