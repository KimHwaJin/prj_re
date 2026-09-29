"""Build and submit Executor service JSON request bodies."""

from __future__ import annotations

import hashlib
import re
from datetime import date
from pathlib import Path
from typing import Any

from agent_config import AgentSettings
from agent_service.runtime.blocking import run_sync, call_io
from agent_service.agents.analysis.state import AnalysisWorkflowState
from service_contracts.executor import ExecutorRequestBody
from agent_service.agents.analysis.artifacts import (
    build_run_artifact_dir,
    write_demo_json,
)
from integrations.executor.client import submit_execution_start
from service_contracts.workflow import NullWorkflowStore, WorkflowStore


def _operation_mode(workflow_document: dict) -> str:
    execution_mode = str(
        workflow_document.get("workflow", {}).get("execution_mode", "static")
    ).lower()
    if execution_mode == "static":
        return "SINGLE"
    if execution_mode == "adaptive":
        return "MULTI"
    raise ValueError(f"unsupported workflow execution_mode: {execution_mode!r}")


def _workflow_id(workflow_document: dict) -> str:
    workflow_id = workflow_document.get("workflow", {}).get("id")
    if not isinstance(workflow_id, str) or not workflow_id.strip():
        raise ValueError("workflow.workflow.id is required")
    return workflow_id


def _idempotency_key(task_id: str, *, action: str = "start") -> str:
    return f"{task_id}:{action}"


def _lineage(cell: dict[str, Any]) -> dict[str, Any]:
    cell_id = str(cell.get("id") or cell.get("step_id") or "workflow_output")
    role = str(cell.get("role") or "workflow_outputs")
    return {
        "skill_name": str(cell.get("skill") or "workflow"),
        "tool_name": str(cell.get("tool") or role),
        "input_parameters": {
            "step_id": str(cell.get("step_id") or cell_id),
            "tool_id": str(cell.get("tool_id") or cell_id),
        },
    }


def _safe_path_component(value: str, *, label: str) -> str:
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError(f"{label} must be a single non-empty path component")
    return value


def _stage_executor_sources(
    settings: AgentSettings,
    *,
    task_id: str,
    notebook_cells: list[dict[str, Any]],
    sequence_start: int,
) -> list[dict[str, str]]:
    relative_dir = Path(
        "d-test",
        date.today().strftime("%Y%m%d"),
        _safe_path_component(task_id, label="task_id"),
    )
    target_dir = settings.executor_shared_input_root / relative_dir
    target_dir.mkdir(parents=True, exist_ok=True)

    sources: list[dict[str, str]] = []
    for index, cell in enumerate(notebook_cells, start=sequence_start):
        code = cell.get("code")
        if not isinstance(code, str) or not code.strip():
            raise RuntimeError("PATH Executor source requires non-empty cell code.")
        cell_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(cell.get("id") or "cell"))
        file_name = f"{index:03d}_{cell_id}.py"
        target_path = target_dir / file_name
        target_path.write_text(code, encoding="utf-8")
        sources.append(
            {
                "path": (relative_dir / file_name).as_posix(),
                "sha256": hashlib.sha256(target_path.read_bytes()).hexdigest(),
            }
        )
    return sources


def _executor_sources(
    settings: AgentSettings,
    *,
    task_id: str,
    notebook_cells: list[dict[str, Any]],
    sequence_start: int,
) -> list[dict[str, str]]:
    if settings.executor_source_type == "INLINE":
        sources: list[dict[str, str]] = []
        for cell in notebook_cells:
            code = cell.get("code")
            if not isinstance(code, str) or not code.strip():
                raise RuntimeError("INLINE Executor source requires non-empty cell code.")
            sources.append({"type": "INLINE", "content": code})
        return sources

    return [
        {"type": "PATH", **source}
        for source in _stage_executor_sources(
            settings,
            task_id=task_id,
            notebook_cells=notebook_cells,
            sequence_start=sequence_start,
        )
    ]


def build_executor_steps(
    settings: AgentSettings,
    *,
    task_id: str,
    notebook_cells: list[dict[str, Any]],
    sequence_start: int = 0,
) -> list[dict[str, Any]]:
    executor_sources = _executor_sources(
        settings,
        task_id=task_id,
        notebook_cells=notebook_cells,
        sequence_start=sequence_start,
    )
    return [
        {
            "sequence": sequence_start + index,
            "payload": {
                "type": "PYTHON_EXECUTE",
                "source": executor_source,
            },
            "step_timeout_seconds": None,
            "lineage": _lineage(cell),
        }
        for index, (cell, executor_source) in enumerate(
            zip(notebook_cells, executor_sources, strict=True)
        )
    ]


def _executor_response_body(submit_response: dict[str, Any]) -> dict[str, Any]:
    body = submit_response.get("body")
    return body if isinstance(body, dict) else {}


def make_build_executor_request(
    settings: AgentSettings,
    submit_start=submit_execution_start,
    workflow_store: WorkflowStore | None = None,
):
    store = workflow_store or NullWorkflowStore()

    async def build_executor_request(state: AnalysisWorkflowState) -> dict:
        artifact_files = dict(state.get("artifact_files", {}))
        notebook_cells = state["notebook"]["cells"]

        operation_mode = _operation_mode(state["workflow"])
        task_id = state["task_id"]
        source_steps = await run_sync(build_executor_steps,
            settings,
            task_id=task_id,
            notebook_cells=notebook_cells,
            sequence_start=0,
        )

        operation_wait_timeout_seconds = (
            settings.executor_operation_wait_timeout_seconds
            if operation_mode == "MULTI"
            else None
        )
        operation_timeout_seconds = settings.executor_operation_timeout_seconds
        idempotency_key = _idempotency_key(task_id)
        body = ExecutorRequestBody(
            idempotency_key=idempotency_key,
            lifecycle={
                "operation_mode": operation_mode,
                "operation_wait_timeout_seconds": operation_wait_timeout_seconds,
            },
            trigger={
                "type": "INTERACTIVE",
                "actor": {
                    "type": "AGENT",
                    "id": task_id,
                },
            },
            runtime={
                "type": "JUPYTER",
                "profile": settings.executor_runtime_profile,
            },
            context={
                "user_id": state["user_id"],
                "task_id": task_id,
                "project_id": state["project_id"],
                "session_id": state["session_id"],
                "workflow_id": _workflow_id(state["workflow"]),
            },
            operation={
                "operation_timeout_seconds": operation_timeout_seconds,
                "spec": {
                    "schema_version": "1.0",
                    "steps": source_steps,
                },
                "metadata": {},
            },
        )
        payload = body.model_dump(mode="json", exclude_none=True)

        request_path = ""
        run_dir = None
        if settings.demo_artifacts_enabled:
            run_dir = await run_sync(build_run_artifact_dir,
                settings,
                user_id=state["user_id"],
                project_id=state["project_id"],
                session_id=state["session_id"],
                task_id=state["task_id"],
            )
            path = await run_sync(write_demo_json, run_dir / "executor_request.json", payload)
            request_path = str(path)
            artifact_files["executor_request"] = str(path)

        if settings.executor_submit_enabled:
            submit_response = await call_io(submit_start, settings, payload)
        else:
            submit_response = {
                "status_code": None,
                "body": {},
                "skipped": True,
                "reason": "EXECUTOR_SUBMIT_ENABLED=false",
            }
        if run_dir is not None:
            response_path = await run_sync(write_demo_json,
                run_dir / "executor_submit_response.json",
                submit_response,
            )
            artifact_files["executor_submit_response"] = str(response_path)
        response_body = _executor_response_body(submit_response)
        response_operation = response_body.get("operation") or {}
        response_state = response_body.get("state") or {}
        executor_operation_steps = response_operation.get("steps") or []
        execution_id = str(response_body.get("execution_id") or "")
        if execution_id:
            await run_sync(store.start_execution,
                execution_id=execution_id,
                catalog_id=state.get("workflow_catalog_id"),
                session_id=state["session_id"],
                task_id=task_id,
                revision=int(state.get("workflow_revision", 1)),
                workflow=state["workflow"],
                status=str(response_state.get("status") or "submitted"),
            )
        return {
            "execution_mode": operation_mode,
            "idempotency_key": idempotency_key,
            "execution_id": execution_id,
            "executor_operation_id": response_operation.get("operation_id", ""),
            "executor_operation_steps": executor_operation_steps,
            "executor_state_version": int(response_state.get("version", 0)),
            "executor_next_sequence": len(source_steps),
            "executor_operation_number": 1,
            "executor_requires_state_version": bool(
                settings.executor_submit_enabled and operation_mode == "MULTI"
            ),
            "executor_wait_phase": (
                "operation_completed"
                if operation_mode == "MULTI"
                else "execution_completed"
            ),
            "executor_request_path": request_path,
            "execution_steps": payload["operation"]["spec"]["steps"],
            "executor_submit_response": submit_response,
            "execution_status": response_state.get(
                "status",
                (
                    "not_submitted"
                    if not settings.executor_submit_enabled
                    else "submitted"
                ),
            ),
            "artifact_files": artifact_files,
            "final_response": {
                "status": (
                    "submitted_to_executor"
                    if settings.executor_submit_enabled
                    else "executor_request_created"
                ),
                "task_id": task_id,
                "execution_mode": operation_mode,
                "idempotency_key": idempotency_key,
                "execution_id": execution_id,
                "executor_operation_id": response_operation.get(
                    "operation_id", ""
                ),
                "executor_operation_steps": executor_operation_steps,
                "executor_request_path": request_path,
                "execution_steps": payload["operation"]["spec"]["steps"],
                "executor_submit_response": submit_response,
                "artifact_files": artifact_files,
            },
            "messages": [
                {
                    "role": "assistant",
                    "name": "executor",
                    "content": (
                        "분석 워크플로우를 실행기에 제출했습니다."
                        if settings.executor_submit_enabled
                        else "실행기 요청 파일을 생성했으며 자동 제출은 생략했습니다."
                    ),
                }
            ],
        }

    return build_executor_request


__all__ = ["build_executor_steps", "make_build_executor_request"]
