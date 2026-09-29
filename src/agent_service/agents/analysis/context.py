"""Project prompt snapshot -> non-persisted inner Agent runtime context."""
from agent_service.context import AgentContext

def context_from_state(state) -> AgentContext:
    return AgentContext(
        model_selection=state.get("model_selection"),
        user_id=str(state.get("user_id") or ""),
        project_id=str(state.get("project_id") or ""),
        session_id=str(state.get("session_id") or ""),
        project_system_prompt=state.get("project_system_prompt") or "",
        project_prompt_version=state.get("project_prompt_version"),
    )
