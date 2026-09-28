"""Collect Executor results after a Redis event resumes the graph."""

from __future__ import annotations

from typing import Any, Callable

from agent_config import AgentSettings
from agent_service.agents.analysis.state import AnalysisWorkflowState
from agent_service.agents.analysis.workflow.execution_notebook_reader import (
    read_current_operation_results,
)
from app.services.workflow_persistence import (
    NullWorkflowStore,
    WorkflowStore,
    effective_workflow_snapshot,
)


def make_collect_execution_results(
    settings: AgentSettings,
    *,
    adaptive: bool,
    read_results: Callable[
        [AgentSettings, dict[str, Any]], list[dict[str, Any]]
    ] = read_current_operation_results,
    workflow_store: WorkflowStore | None = None,
):
    store = workflow_store or NullWorkflowStore()

    def collect_execution_results(state: AnalysisWorkflowState) -> dict:
        execution_id = str(state.get("execution_id") or "")
        if not execution_id:
            raise RuntimeError("automatic result collection requires execution_id")
        event = state.get("execution_event")
        if not isinstance(event, dict):
            raise RuntimeError(
                "execution_event is required after the Redis worker resumes "
                "the graph"
            )
        event_status = str(event["status"])
        event_state_version = int(event["state_version"])
        tool_results = read_results(settings, state)
        update: dict = {
            "execution_event": None,
            "executor_state_version": event_state_version,
            "executor_requires_state_version": False,
            "executor_tool_results": tool_results,
            "executor_result_history": [
                *state.get("executor_result_history", []),
                *tool_results,
            ],
            "execution_status": event_status,
            "final_response": {
                "status": (
                    "adaptive_execution_results_collected"
                    if adaptive
                    else "static_execution_results_collected"
                ),
                "execution_id": execution_id,
                "execution_status": event_status,
                "execution_state_version": event_state_version,
                "results": tool_results,
            },
        }
        if adaptive:
            observations = dict(state.get("adaptive_observations", {}))
            for item in tool_results:
                if item.get("role") != "workflow_outputs":
                    observations[item["tool_id"]] = item["result"]
            executed = list(state.get("adaptive_executed_tool_ids", []))
            executed_result_ids = [
                item["tool_id"]
                for item in tool_results
                if item.get("role") != "workflow_outputs"
                and (item.get("result") or {}).get("status")
                not in {"NOT_EXECUTED", "PENDING", "SKIPPED"}
            ]
            for tool_id in executed_result_ids:
                if tool_id not in executed:
                    executed.append(tool_id)
            update.update(
                {
                    "adaptive_observations": observations,
                    "adaptive_executed_tool_ids": executed,
                }
            )
        else:
            store.finish_execution(
                execution_id=execution_id,
                status=event_status,
                final_workflow=effective_workflow_snapshot(
                    state.get("workflow", {}), {}
                ),
                successful=(
                    event_status == "SUCCEEDED"
                    and not any(
                        (item.get("result") or {}).get("status") == "FAILED"
                        for item in tool_results
                    )
                ),
            )
        return update

    return collect_execution_results


__all__ = ["make_collect_execution_results"]
