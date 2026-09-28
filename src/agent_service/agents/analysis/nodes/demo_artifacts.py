"""Persist approved Workflow JSON for local demo inspection."""

from __future__ import annotations

from agent_config import AgentSettings
from agent_service.agents.analysis.state import AnalysisWorkflowState
from agent_service.agents.analysis.artifacts import (
    build_run_artifact_dir,
    write_demo_json,
)


def make_save_approved_workflow(settings: AgentSettings):
    def save_approved_workflow(state: AnalysisWorkflowState) -> dict:
        if not state.get("approval", {}).get("approved"):
            raise ValueError("only an approved Workflow can be saved")
        if not settings.demo_artifacts_enabled:
            return {}

        run_dir = build_run_artifact_dir(
            settings,
            user_id=state["user_id"],
            project_id=state["project_id"],
            session_id=state["session_id"],
            task_id=state["task_id"],
        )
        path = write_demo_json(run_dir / "workflow.json", state["workflow"])
        return {
            "artifact_files": {
                **state.get("artifact_files", {}),
                "workflow": str(path),
            }
        }

    return save_approved_workflow


__all__ = ["make_save_approved_workflow"]
