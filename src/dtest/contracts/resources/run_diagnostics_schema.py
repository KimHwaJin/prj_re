"""Run-scoped read-only diagnostics; conversation actions use Run/Session APIs."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from dtest.contracts.enums import AgentRunStatus, TaskStatus


class SessionExecutionDiagnostics(BaseModel):
    ownership_held: bool = Field(description="Session execution ownership is recorded; this is not proof of worker liveness.")
    owner_kind: str | None = Field(default=None, description="Recorded ownership kind, such as api_run or executor_event.")
    owner_id: UUID | None = Field(default=None, description="Recorded owner identity; not a public Run routing parameter.")
    owner_process: str | None = Field(default=None, description="Process identity recorded by the owner.")
    acquired_at: datetime | None = Field(default=None, description="Recorded ownership acquisition time.")
    heartbeat_at: datetime | None = Field(default=None, description="Last recorded heartbeat; age alone does not prove termination.")
    recovery_required: bool = Field(default=False, description="Execution ownership or termination needs recovery verification.")
    recovery_reason: str | None = Field(default=None, description="Recorded reason for recovery verification.")


class SessionWorkDiagnostics(BaseModel):
    resources_active: bool = Field(description="Whether the linked User, Project and Session are all active.")
    has_unfinished_work: bool = Field(description="Session-wide unfinished work or uncertain ownership, not just this Run.")
    can_start_new_run: bool = Field(description="Conservative fresh-input diagnostic snapshot, not HITL admission or a reservation. UI uses Session availability; POST rechecks.")
    blocking_reasons: list[Literal["resources_inactive", "unfinished_command", "unfinished_task", "unfinished_run", "execution_held_or_uncertain"]] = Field(description="Internal session-wide reasons blocking a fresh input.")
    execution: SessionExecutionDiagnostics = Field(description="Current Session ownership record, possibly held by another Run.")


class TaskDiagnostics(BaseModel):
    task_id: UUID = Field(description="Internal Task record associated with the latest invocation of this public Run.")
    graph_task_id: UUID | None = Field(description="Agent graph analysis identity; distinct from the service Task ID.")
    root_run_id: UUID | None = Field(description="Recorded first invocation of the Task.")
    checkpoint_run_id: UUID | None = Field(description="Recorded LangGraph checkpoint identity; not an API routing parameter.")
    trigger_message_id: UUID | None = Field(description="Message that triggered the internal Task.")
    trigger_type: str = Field(description="Internal trigger classification.")
    status: TaskStatus = Field(description="Internal Task state; waiting_input can include Executor waiting. UI uses public Run status.")
    is_unfinished: bool = Field(description="This Task is nonterminal or requires recovery, independent of other Session work.")
    lock_owner: str | None = Field(description="Recorded Task lease owner; current graph ownership is session_work.execution.")
    heartbeat_at: datetime | None = Field(description="Last recorded Task lease heartbeat, not proof of liveness.")
    lease_expires_at: datetime | None = Field(description="Recorded Task lease expiry, not permission to release Session ownership.")
    cancel_requested_at: datetime | None = Field(description="Task cancellation request time; not proof of actual termination.")
    failure_reason: str | None = Field(description="Recorded internal Task failure reason.")
    recovery_required: bool = Field(description="Task recovery verification is required.")
    created_at: datetime = Field(description="Task record creation time.")
    updated_at: datetime = Field(description="Task record last update time.")
    completed_at: datetime | None = Field(description="Recorded Task completion time.")


class RunDiagnosticsResource(BaseModel):
    run_id: UUID = Field(description="Stable public Run ID, including requests made through legacy invocation IDs.")
    session_id: UUID = Field(description="Session owning this public Run.")
    observed_at: datetime = Field(description="Database statement observation time for this diagnostic snapshot.")
    task: TaskDiagnostics | None = Field(description="Latest invocation's internal Task, or null when absent or not coherently linked to this Session/Run.")
    session_work: SessionWorkDiagnostics = Field(description="Current work across the entire Session; distinct from this Run's Task status.")


class RunInvocationResource(BaseModel):
    invocation_id: UUID = Field(description="Internal execution segment ID, newly created for each resume.")
    run_id: UUID = Field(description="Stable public Run ID shared by all listed execution segments.")
    task_id: UUID | None = Field(description="Recorded internal Task reference; legacy taskless invocations have null.")
    session_id: UUID = Field(description="Session owning this invocation.")
    status: AgentRunStatus = Field(description="Internal invocation state, not the aggregate public Run state.")
    attempt_count: int = Field(description="Worker claim attempt count, including the initial attempt.")
    next_attempt_at: datetime | None = Field(description="Next eligible worker retry time.")
    cancel_requested_at: datetime | None = Field(description="Invocation cancellation request time.")
    cancel_reason: str | None = Field(description="Recorded cancellation reason.")
    failure: dict | None = Field(description="Recorded failure for this execution segment; producer-defined diagnostic object.")
    created_at: datetime = Field(description="Invocation record creation time.")
    updated_at: datetime = Field(description="Invocation record last update time.")
    started_at: datetime | None = Field(description="Invocation execution start time.")
    completed_at: datetime | None = Field(description="Invocation completion time.")
