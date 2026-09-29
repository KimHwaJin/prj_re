"""Workflow storage port and pure snapshots; no database imports."""
from __future__ import annotations
from copy import deepcopy
from typing import Any, Protocol

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
