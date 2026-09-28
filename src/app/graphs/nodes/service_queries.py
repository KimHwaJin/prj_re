"""Simple FAQ answering and a file-lookup placeholder node."""

from __future__ import annotations

from app.agents.orchestration.agent_interfaces import invoke_typed
from app.agents.orchestration.dependencies import AgentDependencies
from app.graphs.message_utils import as_message_content
from app.graphs.state.analysis_workflow_state import AnalysisWorkflowState
from app.schemas.agents.orchestration_schema import FaqOutput, PlaceholderResponse


def _service_query(state: AnalysisWorkflowState) -> str:
    return (state.get("action_query") or state["user_request"]).strip()


def make_faq_node(deps: AgentDependencies):
    def faq_node(state: AnalysisWorkflowState) -> dict:
        output = invoke_typed(
            deps.faq_agent,
            {"user_request": _service_query(state)},
            FaqOutput,
        )
        payload = output.model_dump(mode="json")
        # TODO(CRUD): 에이전트 메시지 POST
        return {
            "service_response": {
                "service": "faq",
                "query": _service_query(state),
                "response": payload,
            },
            "messages": [
                {
                    "role": "assistant",
                    "name": "faq",
                    "content": as_message_content(payload),
                }
            ],
        }

    return faq_node


def make_file_lookup_node(deps: AgentDependencies):
    def file_lookup_node(state: AnalysisWorkflowState) -> dict:
        output = invoke_typed(
            deps.file_lookup_agent,
            {"user_request": _service_query(state)},
            PlaceholderResponse,
        )
        payload = output.model_dump(mode="json")
        # TODO(CRUD): 에이전트 메시지 POST
        return {
            "service_response": {
                "service": "file_lookup",
                "query": _service_query(state),
                "response": payload,
            },
            "messages": [
                {
                    "role": "assistant",
                    "name": "file_lookup",
                    "content": as_message_content(payload),
                }
            ],
        }

    return file_lookup_node
