"""Adapt durable Worker events to the existing Executor result nodes."""

from __future__ import annotations

from typing import Any

from agent_service.agents.analysis.state import AnalysisWorkflowState
from app.services.workflow_persistence import (
    NullWorkflowStore,
    WorkflowStore,
    effective_workflow_snapshot,
)


def apply_redis_execution_event(state: AnalysisWorkflowState) -> dict[str, Any]:
    action = state.get("ew_pending")
    if not isinstance(action, dict):
        raise ValueError("ew_pending is required after an Executor interrupt")
    event = action.get("event")
    if not isinstance(event, dict):
        raise ValueError("ew_pending.event must be an object")
    if str(action.get("execution_id") or "") != str(state.get("execution_id") or ""):
        raise ValueError("Executor event does not match the active execution")

    phase = state.get("executor_wait_phase")
    event_type = str(event.get("event_type") or "")
    expected_type = (
        "execution.operation_completed"
        if phase == "operation_completed"
        else "execution.completed"
    )
    if event_type != expected_type:
        raise ValueError(
            f"expected {expected_type!r} while waiting for {phase!r}, "
            f"received {event_type!r}"
        )

    payload = event.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("Executor event payload must be an object")
    executor_state = payload.get("state")
    if not isinstance(executor_state, dict):
        executor_state = {}
    continuation = payload.get("continuation")
    if not isinstance(continuation, dict):
        continuation = {}
    status = str(
        payload.get("status")
        or executor_state.get("status")
        or ("SUCCEEDED" if event_type == "execution.completed" else "UNKNOWN")
    )
    state_version = int(
        continuation.get("expected_version")
        or payload.get("state_version")
        or executor_state.get("version")
        or state.get("executor_state_version", 0)
    )
    normalized = {
        "session_id": state.get("session_id"),
        "task_id": state["task_id"],
        "execution_id": state["execution_id"],
        "operation_number": state.get("executor_operation_number", 1),
        "status": status,
        "state_version": state_version,
        "response": payload,
    }
    return {
        "execution_event": normalized,
        "execution_events": [*state.get("execution_events", []), event],
        "execution_status": status,
        "executor_state_version": state_version,
    }


def make_collect_final_execution_event(
    workflow_store: WorkflowStore | None = None,
):
    store = workflow_store or NullWorkflowStore()

    def collect_final_execution_event(
        state: AnalysisWorkflowState,
    ) -> dict[str, Any]:
        event = state.get("execution_event") or {}
        status = str(event.get("status") or "UNKNOWN")
        response = event.get("response") or {}
        event_error = response.get("error") if isinstance(response, dict) else None
        failed_results = [
            item
            for item in state.get("executor_result_history", [])
            if (item.get("result") or {}).get("status") == "FAILED"
        ]
        error_messages = list(
            dict.fromkeys(
                str(message)
                for message in [
                    *[
                        (item.get("result") or {}).get("error_message")
                        for item in failed_results
                    ],
                    (
                        event_error.get("message")
                        if isinstance(event_error, dict)
                        else None
                    ),
                ]
                if message
            )
        )
        final_workflow = effective_workflow_snapshot(
            state.get("workflow", {}),
            state.get("adaptive_runtime_decisions", {}),
        )
        store.finish_execution(
            execution_id=str(state.get("execution_id") or ""),
            status=status,
            final_workflow=final_workflow,
            successful=status == "SUCCEEDED" and not failed_results,
        )
        if status == "SUCCEEDED":
            message = "분석 실행이 완료되었습니다."
            if error_messages:
                message += f" 일부 셀 실패 {len(failed_results)}건이 있습니다."
        elif status == "CANCELLED":
            message = "분석 실행이 일부 셀 실패로 종료되었습니다."
        else:
            message = f"분석 실행이 {status} 상태로 종료되었습니다."
        if error_messages:
            message += " 원인: " + " | ".join(error_messages)
        return {
            "execution_event": None,
            "execution_status": status,
            "final_response": {
                "status": "adaptive_execution_completed",
                "execution_id": state["execution_id"],
                "execution_status": status,
                "execution_state_version": int(
                    event.get("state_version")
                    or state.get("executor_state_version", 0)
                ),
                "failed_cell_count": len(failed_results),
                "errors": error_messages,
                "message": message,
            },
            "messages": [
                {
                    "role": "assistant",
                    "name": "executor_result",
                    "content": message,
                }
            ],
        }

    return collect_final_execution_event


collect_final_execution_event = make_collect_final_execution_event()


__all__ = [
    "apply_redis_execution_event",
    "collect_final_execution_event",
    "make_collect_final_execution_event",
]
