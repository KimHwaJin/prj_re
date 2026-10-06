"""Create reusable agents through the same prompt/discovery/schema middleware."""

from dtest.agent_service.factory import build_role_agent, json_output
from dtest.agent_service.middleware import (
    ProjectPromptMiddleware,
    SessionAnalysisMiddleware,
)
from dtest.agent_service.middleware.discovery import (
    MetadataDiscoveryMiddleware,
)
from dtest.agent_service.agents.analysis.planning.proposals import (
    RevisionReply,
)
from dtest.contracts.workflow_validation import workflow_schema
from .._prompts import load_prompt
import json
from langchain.tools import tool


def build_agent(
    model,
    catalog,
    *,
    discovery_max_rounds=4,
    structured_output_mode="prompt_json",
    validate_response=None,
    session_context_max_chars=16000,
    store=None,
):
    @tool
    def read_tool_source(tool_id: str) -> dict:
        """Read a deployed analysis function for execution-local modification; never executes Python."""
        source = catalog.sources.get(tool_id)
        return (
            {"tool_id": tool_id, **source}
            if source
            else {"error": "Unknown deployed Tool"}
        )

    prompt = (
        load_prompt(__package__)
        + "\nPlan definition schema:\n"
        + json.dumps(workflow_schema(), ensure_ascii=False)
    )
    return build_role_agent(
        model,
        name="analysis_plan_revision",
        system_prompt=prompt,
        tools=[*catalog.metadata_tools(), read_tool_source],
        middleware=[
            ProjectPromptMiddleware(),
            SessionAnalysisMiddleware(max_chars=session_context_max_chars),
            MetadataDiscoveryMiddleware(
                max_rounds=discovery_max_rounds,
                final_instruction=(
                    "Return exactly RevisionReply JSON: kind, message, "
                    "plans with definition/input_values/functions; ask "
                    "clarification when "
                    "needed."
                ),
            ),
        ],
        output_type=RevisionReply,
        decode=json_output(RevisionReply),
        structured_output_mode=structured_output_mode,
        max_validation_attempts=2,
        validate_response=validate_response,
        store=store,
    )
