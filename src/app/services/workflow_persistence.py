"""Durable reusable Workflow catalog and per-execution history."""

from __future__ import annotations

from copy import deepcopy
import os
from typing import Any, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from psycopg import connect
from psycopg.types.json import Jsonb


class WorkflowStore(Protocol):
    def save_catalog_workflow(
        self,
        *,
        session_id: str,
        task_id: str,
        intent: str,
        revision: int,
        workflow: dict[str, Any],
    ) -> str: ...

    def start_execution(
        self,
        *,
        execution_id: str,
        catalog_id: str | None,
        session_id: str,
        task_id: str,
        revision: int,
        workflow: dict[str, Any],
        status: str,
    ) -> None: ...

    def record_adaptive_round(
        self,
        *,
        execution_id: str,
        decision_round: int,
        changes: list[dict[str, Any]],
        effective_workflow: dict[str, Any],
    ) -> None: ...

    def finish_execution(
        self,
        *,
        execution_id: str,
        status: str,
        final_workflow: dict[str, Any],
        successful: bool | None = None,
    ) -> None: ...


class NullWorkflowStore:
    """No-op store used by unit tests and callers without application storage."""

    def save_catalog_workflow(self, **_: Any) -> str:
        return ""

    def start_execution(self, **_: Any) -> None:
        return None

    def record_adaptive_round(self, **_: Any) -> None:
        return None

    def finish_execution(self, **_: Any) -> None:
        return None


def reusable_workflow_snapshot(workflow: dict[str, Any]) -> dict[str, Any]:
    """Remove execution-bound values while retaining the complete definition."""

    document = deepcopy(workflow)
    definition = document.get("workflow")
    if not isinstance(definition, dict):
        return document

    context = definition.get("context")
    if isinstance(context, dict):
        context.pop("output_dir", None)

    input_schema = definition.get("input_schema")
    if isinstance(input_schema, dict):
        existing_unresolved = {
            str(item.get("name")): item
            for item in definition.get("unresolved_inputs") or []
            if isinstance(item, dict) and item.get("name")
        }
        definition["inputs"] = {}
        definition["input_provenance"] = {}
        definition["unresolved_inputs"] = [
            {
                "name": name,
                "question": str(
                    existing_unresolved.get(name, {}).get("question")
                    or f"{name} 값을 입력해주세요."
                ),
                "required_for": list(
                    existing_unresolved.get(name, {}).get("required_for") or []
                ),
            }
            for name, schema in input_schema.items()
            if not isinstance(schema, dict) or schema.get("required", True)
        ]
        if definition["unresolved_inputs"]:
            definition["status"] = "needs_input"
    return document


def effective_workflow_snapshot(
    workflow: dict[str, Any], runtime_decisions: dict[str, str]
) -> dict[str, Any]:
    """Materialize runtime decisions without overwriting declared execution rules."""

    document = deepcopy(workflow)
    definition = document.get("workflow")
    if not isinstance(definition, dict):
        return document
    for step in definition.get("steps") or []:
        if not isinstance(step, dict):
            continue
        for tool in step.get("tools") or []:
            if not isinstance(tool, dict):
                continue
            tool_id = str(tool.get("id") or "")
            if tool_id in runtime_decisions:
                tool["runtime_decision"] = runtime_decisions[tool_id]
    return document


class PostgresWorkflowStore:
    """Small synchronous repository used from synchronous LangGraph nodes."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url.replace(
            "postgresql+psycopg://", "postgresql://", 1
        )

    def save_catalog_workflow(
        self,
        *,
        session_id: str,
        task_id: str,
        intent: str,
        revision: int,
        workflow: dict[str, Any],
    ) -> str:
        definition = workflow.get("workflow") or {}
        workflow_id = str(definition.get("id") or "")
        if not workflow_id:
            raise ValueError("workflow.workflow.id is required for catalog storage")
        catalog_id = uuid5(
            NAMESPACE_URL,
            f"dtest-agent/workflow/{task_id}/{workflow_id}/{revision}",
        )
        # Preserve the exact candidate, including its original needs_input state.
        # Runtime-bound values are cleared when this catalog entry is loaded for
        # recommendation, not while writing the source-of-truth snapshot.
        snapshot = deepcopy(workflow)
        status = str((snapshot.get("workflow") or {}).get("status") or "needs_input")
        with connect(self.database_url) as conn:
            conn.execute(
                """INSERT INTO workflow_catalog
                (catalog_id, workflow_id, revision, source_session_id,
                 source_task_id, intent, workflow_status, workflow)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (source_task_id, workflow_id, revision) DO UPDATE SET
                    intent=EXCLUDED.intent,
                    workflow_status=EXCLUDED.workflow_status,
                    workflow=EXCLUDED.workflow,
                    updated_at=now()
                """,
                (
                    catalog_id,
                    workflow_id,
                    revision,
                    session_id,
                    task_id,
                    intent,
                    status,
                    Jsonb(snapshot),
                ),
            )
        return str(catalog_id)

    def start_execution(
        self,
        *,
        execution_id: str,
        catalog_id: str | None,
        session_id: str,
        task_id: str,
        revision: int,
        workflow: dict[str, Any],
        status: str,
    ) -> None:
        definition = workflow.get("workflow") or {}
        workflow_id = str(definition.get("id") or "")
        if not execution_id or not workflow_id:
            return
        catalog_uuid = UUID(catalog_id) if catalog_id else None
        with connect(self.database_url) as conn:
            conn.execute(
                """INSERT INTO workflow_executions
                (execution_id, catalog_id, workflow_id, workflow_revision,
                 session_id, task_id, original_workflow, execution_status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (execution_id) DO UPDATE SET
                    catalog_id=COALESCE(workflow_executions.catalog_id,
                                        EXCLUDED.catalog_id),
                    original_workflow=EXCLUDED.original_workflow,
                    execution_status=CASE
                        WHEN workflow_executions.finished_at IS NULL
                        THEN EXCLUDED.execution_status
                        ELSE workflow_executions.execution_status
                    END,
                    updated_at=now()
                """,
                (
                    UUID(execution_id),
                    catalog_uuid,
                    workflow_id,
                    revision,
                    session_id,
                    task_id,
                    Jsonb(workflow),
                    status or "submitted",
                ),
            )
            if catalog_uuid:
                conn.execute(
                    """UPDATE workflow_catalog
                    SET source_execution_id=%s, updated_at=now()
                    WHERE catalog_id=%s""",
                    (UUID(execution_id), catalog_uuid),
                )

    def record_adaptive_round(
        self,
        *,
        execution_id: str,
        decision_round: int,
        changes: list[dict[str, Any]],
        effective_workflow: dict[str, Any],
    ) -> None:
        if not execution_id:
            return
        with connect(self.database_url) as conn:
            conn.execute(
                """INSERT INTO workflow_adaptive_history
                (execution_id, decision_round, changes, effective_workflow)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT (execution_id, decision_round) DO UPDATE SET
                    changes=EXCLUDED.changes,
                    effective_workflow=EXCLUDED.effective_workflow
                """,
                (
                    UUID(execution_id),
                    decision_round,
                    Jsonb(changes),
                    Jsonb(effective_workflow),
                ),
            )

    def finish_execution(
        self,
        *,
        execution_id: str,
        status: str,
        final_workflow: dict[str, Any],
        successful: bool | None = None,
    ) -> None:
        if not execution_id:
            return
        is_successful = status == "SUCCEEDED" if successful is None else successful
        with connect(self.database_url) as conn:
            row = conn.execute(
                """SELECT catalog_id, finished_at
                FROM workflow_executions
                WHERE execution_id=%s
                FOR UPDATE""",
                (UUID(execution_id),),
            ).fetchone()
            if row is None:
                return
            catalog_id, finished_at = row
            conn.execute(
                """UPDATE workflow_executions
                SET final_workflow=%s, execution_status=%s,
                    finished_at=now(), updated_at=now()
                WHERE execution_id=%s
                """,
                (Jsonb(final_workflow), status, UUID(execution_id)),
            )
            if catalog_id and finished_at is None:
                conn.execute(
                    f"""UPDATE workflow_catalog SET
                    validation_status=%s,
                    {'success_count=success_count+1' if is_successful else 'failure_count=failure_count+1'},
                    last_execution_status=%s,
                    last_executed_at=now(), updated_at=now()
                    WHERE catalog_id=%s""",
                    ("validated" if is_successful else "failed", status, catalog_id),
                )


def workflow_store_from_environment() -> WorkflowStore:
    """Build application persistence from the existing Worker database URL."""

    database_url = (
        os.environ.get("WORKFLOW_DATABASE_URL", "").strip()
        or os.environ.get("EW_DATABASE_URL", "").strip()
    )
    return PostgresWorkflowStore(database_url) if database_url else NullWorkflowStore()


__all__ = [
    "NullWorkflowStore",
    "PostgresWorkflowStore",
    "WorkflowStore",
    "effective_workflow_snapshot",
    "reusable_workflow_snapshot",
    "workflow_store_from_environment",
]
