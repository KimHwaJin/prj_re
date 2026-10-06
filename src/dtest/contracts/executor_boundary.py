"""Durable execution binding port and checkpoint fields."""
from __future__ import annotations
from typing import Any, Protocol, TypedDict
from uuid import UUID

class ExecutorBoundaryInput(TypedDict):
    """Values the Agent creates immediately before waiting for Executor."""

    task_id: str
    execution_id: str


class ExecutorBoundaryState(ExecutorBoundaryInput, total=False):
    """Fields needed for durable Worker delivery and replay recovery."""

    ew_pending: dict[str, Any]
    ew_receipts: dict[str, str]
    ew_sequences: dict[str, int]


class ExecutionBindings(Protocol):
    """Small part of the Worker store used inside an Agent graph."""

    async def register(
        self,
        *,
        execution_id: UUID,
        session_id: str,
        task_id: str,
    ) -> None: ...
