"""Read-only operational diagnostics; user execution commands belong to Runs."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from api_service.core.enums import AgentRunStatus, TaskStatus


class SessionExecutionResource(BaseModel):
    ownership_held: bool
    owner_kind: str | None = None
    owner_id: UUID | None = None
    owner_process: str | None = None
    acquired_at: datetime | None = None
    heartbeat_at: datetime | None = Field(default=None, description="Last recorded heartbeat, not proof of liveness or termination.")
    recovery_required: bool = False
    recovery_reason: str | None = None


class SessionWorkResource(BaseModel):
    resources_active: bool
    has_unfinished_work: bool
    can_start_new_run: bool = Field(description="Conservative snapshot for a fresh input, not HITL resume or a reservation; POST rechecks admission.")
    blocking_reasons: list[Literal["resources_inactive", "unfinished_task", "unfinished_run", "unfinished_llm", "execution_held_or_uncertain"]]
    execution: SessionExecutionResource


class TaskResource(BaseModel):
    task_id: UUID
    public_run_id: UUID | None
    graph_task_id: UUID | None
    root_run_id: UUID | None
    checkpoint_run_id: UUID | None
    session_id: UUID
    trigger_message_id: UUID | None
    trigger_type: str
    status: TaskStatus = Field(description="Internal Task state; waiting_input can also represent Executor waiting. Use public Run status for UI.")
    is_unfinished: bool
    lock_owner: str | None = Field(description="Task lease owner; current graph ownership is session_work.execution.")
    heartbeat_at: datetime | None
    lease_expires_at: datetime | None
    cancel_requested_at: datetime | None
    failure_reason: str | None
    recovery_required: bool
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    observed_at: datetime
    session_work: SessionWorkResource


class TaskInvocationResource(BaseModel):
    invocation_id: UUID = Field(description="Internal execution segment ID, not the stable public Run ID.")
    public_run_id: UUID
    task_id: UUID
    session_id: UUID
    status: AgentRunStatus
    attempt_count: int
    next_attempt_at: datetime | None
    cancel_requested_at: datetime | None
    cancel_reason: str | None
    failure: dict | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
