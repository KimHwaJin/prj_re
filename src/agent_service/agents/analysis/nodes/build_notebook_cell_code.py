"""Rule-based approved-Workflow notebook code generation node."""

from __future__ import annotations

from agent_config import AgentSettings
from agent_service.agents.analysis.message_utils import as_message_content
from agent_service.agents.analysis.state import AnalysisWorkflowState
from agent_service.agents.analysis.artifacts import build_run_artifact_dir
from agent_service.agents.analysis.workflow.rule_based_notebook_generator import (
    APP_SOURCE_ROOT,
    build_notebook,
    write_cell_code_files,
)
from agent_service.agents.analysis.workflow.data_load_steps import (
    validate_target_data_role_lineage,
)
from agent_service.agents.analysis.schemas.agents.orchestration_schema import NotebookGenerationOutput


def make_build_notebook_code(settings: AgentSettings):
    def build_notebook_code(state: AnalysisWorkflowState) -> dict:
        validate_target_data_role_lineage(
            state["workflow"],
            state["data_selection"],
        )
        artifact = build_notebook(
            state["workflow"],
            project_root=APP_SOURCE_ROOT,
            job_id=state["task_id"],
        )
        output = NotebookGenerationOutput(
            message=(
                "\uc2b9\uc778\ub41c Workflow\uc5d0\uc11c \ub8f0\ubca0\uc774\uc2a4\ub85c "
                "Notebook \ucf54\ub4dc\ub97c \uc0dd\uc131\ud588\uc2b5\ub2c8\ub2e4."
            ),
            cells=[
                {
                    "order": cell["order"],
                    "id": cell["id"],
                    "cell_type": cell["cell_type"],
                    "role": cell["role"],
                    "skill": cell["skill"],
                    "code": cell["code"],
                    "step_id": cell["step_id"],
                    "tool_id": cell["tool_id"],
                    "tool": cell["tool"],
                }
                for cell in artifact["cells"]
            ],
        )
        payload = output.model_dump(mode="json")
        artifact_files = dict(state.get("artifact_files", {}))
        if settings.demo_artifacts_enabled:
            run_dir = build_run_artifact_dir(
                settings,
                user_id=state["user_id"],
                project_id=state["project_id"],
                session_id=state["session_id"],
                task_id=state["task_id"],
            )
            paths = write_cell_code_files(artifact, run_dir / "cells")
            artifact_files["cells"] = [str(path) for path in paths]
        return {
            "notebook": payload,
            "adaptive_pending_tool_id": artifact["workflow"].get(
                "pending_conditional_tool_id"
            ),
            "adaptive_generated_tool_ids": [
                cell["tool_id"]
                for cell in artifact["cells"]
                if cell.get("tool_id")
            ],
            "adaptive_runtime_decisions": {},
            "adaptive_execution_plan": {
                "workflow_id": state["workflow"]["workflow"]["id"],
                "runtime_decisions": {},
            },
            "adaptive_observations": {},
            "adaptive_executed_tool_ids": [],
            "adaptive_round": 1,
            "adaptive_status": (
                "waiting_for_execution_results"
                if artifact["workflow"].get("pending_conditional_tool_id")
                else "complete"
            ),
            "artifact_files": artifact_files,
            "messages": [
                {
                    "role": "assistant",
                    "name": "rule_based_notebook_generator",
                    "content": as_message_content(
                        {
                            "message": output.message,
                            "cell_count": len(output.cells),
                        }
                    ),
                }
            ],
        }

    return build_notebook_code
