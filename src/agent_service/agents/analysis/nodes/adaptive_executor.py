"""Submit subsequent Operations and finalize adaptive MULTI Executions."""

from __future__ import annotations

from typing import Any

from agent_config import AgentSettings
from agent_service.agents.analysis.message_utils import as_message_content
from agent_service.agents.analysis.nodes.executor_request import build_executor_steps
from agent_service.agents.analysis.state import AnalysisWorkflowState
from agent_service.agents.analysis.schemas.agents.orchestration_schema import (
    ExecutorCancelRequestBody,
    ExecutorContinueRequestBody,
    ExecutorFinalizeRequestBody,
)
from agent_service.agents.analysis.artifacts import build_run_artifact_dir, write_demo_json
from app.services.executor_client import (
    submit_execution_cancel,
    submit_execution_continue,
    submit_execution_finish,
)


def _response_body(response: dict[str, Any]) -> dict[str, Any]:
    body = response.get("body")
    return body if isinstance(body, dict) else {}


def _skipped_response(reason: str) -> dict[str, Any]:
    return {"status_code": None, "body": {}, "skipped": True, "reason": reason}


def make_submit_adaptive_operation(
    settings: AgentSettings,
    submit_continue=submit_execution_continue,
):
    def submit_adaptive_operation(state: AnalysisWorkflowState) -> dict:
        execution_id = str(state.get("execution_id") or "")
        if settings.executor_submit_enabled and not execution_id:
            raise RuntimeError("adaptive Operation requires execution_id")

        cells = state.get("notebook", {}).get("cells") or []
        if not cells:
            raise RuntimeError("adaptive Operation requires generated code cells")
        sequence_start = int(state.get("executor_next_sequence", 0))
        steps = build_executor_steps(
            settings,
            task_id=state["task_id"],
            notebook_cells=cells,
            sequence_start=sequence_start,
        )
        operation_number = int(state.get("executor_operation_number", 1)) + 1
        payload = ExecutorContinueRequestBody(
            idempotency_key=f"{state['task_id']}:operation:{operation_number}",
            expected_version=int(state.get("executor_state_version", 0)),
            operation_timeout_seconds=settings.executor_operation_timeout_seconds,
            spec={"schema_version": "1.0", "steps": steps},
            metadata={"reason": "adaptive_followup", "round": state.get("adaptive_round")},
            actor={"type": "AGENT", "id": state["task_id"]},
        ).model_dump(mode="json", exclude_none=True)

        artifact_files = dict(state.get("artifact_files", {}))
        if settings.demo_artifacts_enabled:
            run_dir = build_run_artifact_dir(
                settings,
                user_id=state["user_id"],
                project_id=state["project_id"],
                session_id=state["session_id"],
                task_id=state["task_id"],
            )
            path = write_demo_json(
                run_dir / f"executor_operation_{operation_number}_request.json",
                payload,
            )
            requests = list(artifact_files.get("executor_operation_requests", []))
            requests.append(str(path))
            artifact_files["executor_operation_requests"] = requests

        response = (
            submit_continue(settings, execution_id, payload)
            if settings.executor_submit_enabled
            else _skipped_response("EXECUTOR_SUBMIT_ENABLED=false")
        )
        body = _response_body(response)
        operation = body.get("operation") or {}
        response_state = body.get("state") or {}
        return {
            "executor_operation_id": operation.get("operation_id", ""),
            "executor_operation_steps": operation.get("steps") or [],
            "executor_state_version": int(
                response_state.get("version", state.get("executor_state_version", 0))
            ),
            "executor_next_sequence": sequence_start + len(steps),
            "executor_operation_number": operation_number,
            "executor_requires_state_version": bool(settings.executor_submit_enabled),
            "execution_steps": list(state.get("execution_steps", [])) + steps,
            "executor_submit_response": response,
            "execution_status": response_state.get("status", "not_submitted"),
            "artifact_files": artifact_files,
            "adaptive_status": "waiting_for_execution_results",
            "executor_wait_phase": "operation_completed",
            "final_response": {
                "status": "adaptive_operation_submitted"
                if settings.executor_submit_enabled
                else "adaptive_operation_request_created",
                "execution_id": execution_id,
                "operation_number": operation_number,
                "steps": steps,
                "artifact_files": artifact_files,
            },
            "messages": [{
                "role": "assistant",
                "name": "executor",
                "content": as_message_content({
                    "status": "adaptive_operation_submitted",
                    "operation_number": operation_number,
                }),
            }],
        }

    return submit_adaptive_operation


def make_finalize_adaptive_execution(
    settings: AgentSettings,
    submit_finish=submit_execution_finish,
):
    def finalize_adaptive_execution(state: AnalysisWorkflowState) -> dict:
        execution_id = str(state.get("execution_id") or "")
        if settings.executor_submit_enabled and not execution_id:
            raise RuntimeError("adaptive finalize requires execution_id")
        payload = ExecutorFinalizeRequestBody(
            idempotency_key=f"{state['task_id']}:finalize",
            expected_version=int(state.get("executor_state_version", 0)),
            actor={"type": "AGENT", "id": state["task_id"]},
        ).model_dump(mode="json")

        artifact_files = dict(state.get("artifact_files", {}))
        if settings.demo_artifacts_enabled:
            run_dir = build_run_artifact_dir(
                settings,
                user_id=state["user_id"],
                project_id=state["project_id"],
                session_id=state["session_id"],
                task_id=state["task_id"],
            )
            path = write_demo_json(run_dir / "executor_finalize_request.json", payload)
            artifact_files["executor_finalize_request"] = str(path)

        response = (
            submit_finish(settings, execution_id, payload)
            if settings.executor_submit_enabled
            else _skipped_response("EXECUTOR_SUBMIT_ENABLED=false")
        )
        body = _response_body(response)
        response_state = body.get("state") or {}
        return {
            "executor_state_version": int(
                response_state.get("version", state.get("executor_state_version", 0))
            ),
            "executor_submit_response": response,
            "execution_status": response_state.get("status", "not_submitted"),
            "adaptive_status": "finalizing",
            "executor_wait_phase": "execution_completed",
            "executor_requires_state_version": False,
            "artifact_files": artifact_files,
            "final_response": {
                "status": "adaptive_finalizing"
                if settings.executor_submit_enabled
                else "adaptive_finalize_request_created",
                "execution_id": execution_id,
                "artifact_files": artifact_files,
            },
            "messages": [{
                "role": "assistant",
                "name": "executor",
                "content": as_message_content({"status": "adaptive_finalizing"}),
            }],
        }

    return finalize_adaptive_execution


def make_cancel_adaptive_execution(
    settings: AgentSettings,
    submit_cancel=submit_execution_cancel,
):
    def cancel_adaptive_execution(state: AnalysisWorkflowState) -> dict:
        execution_id = str(state.get("execution_id") or "")
        if settings.executor_submit_enabled and not execution_id:
            raise RuntimeError("adaptive cancel requires execution_id")
        failed_results = [
            item
            for item in state.get("executor_tool_results", [])
            if (item.get("result") or {}).get("status") == "FAILED"
        ]
        reason = "; ".join(
            str((item.get("result") or {}).get("error_message") or "Step failed")
            for item in failed_results
        ) or "Adaptive execution contains failed Steps"
        payload = ExecutorCancelRequestBody(
            idempotency_key=f"{state['task_id']}:cancel-after-step-failure",
            reason=reason[:2000],
            actor={"type": "AGENT", "id": state["task_id"]},
        ).model_dump(mode="json", exclude_none=True)
        response = (
            submit_cancel(settings, execution_id, payload)
            if settings.executor_submit_enabled
            else _skipped_response("EXECUTOR_SUBMIT_ENABLED=false")
        )
        body = _response_body(response)
        response_state = body.get("state") or {}
        return {
            "executor_submit_response": response,
            "execution_status": response_state.get(
                "status", "cancel_requested"
            ),
            "adaptive_status": "cancelling_after_step_failure",
            "executor_wait_phase": "execution_completed",
            "executor_requires_state_version": False,
            "final_response": {
                "status": "adaptive_execution_cancelling_after_step_failure",
                "execution_id": execution_id,
                "failed_results": failed_results,
            },
            "messages": [
                {
                    "role": "assistant",
                    "name": "executor",
                    "content": as_message_content(
                        {
                            "status": "adaptive_execution_cancelling_after_step_failure",
                            "failed_results": failed_results,
                        }
                    ),
                }
            ],
        }

    return cancel_adaptive_execution


__all__ = [
    "make_cancel_adaptive_execution",
    "make_finalize_adaptive_execution",
    "make_submit_adaptive_operation",
]
