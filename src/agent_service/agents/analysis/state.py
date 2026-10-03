"""Flat durable channels and explicit per-node read boundaries.

Grouping Python types does not rename or nest stored checkpoint channels.
Node input schemas constrain reads, not writes or public SSE redaction.
"""
from typing import TypedDict, get_type_hints


class OwnerState(TypedDict, total=False):
    """Trusted ownership; supplied by the service, not by role output."""

    user_id: str
    project_id: str
    session_id: str


class RequestState(TypedDict, total=False):
    """Input snapshot for this request. The service supplies its prompt/model identity."""

    run_id: str
    thread_id: str
    request_id: str
    user_request: str
    trigger_message_id: str
    project_system_prompt: str
    project_prompt_version: int
    model_selection: dict
    initial_request_identity: dict | None


class SessionState(TypedDict, total=False):
    """Bounded conversation/evidence and the session kernel survive a new request."""

    history: list[dict]
    last_analysis_context: dict | None
    kernel_profile: str


class PresentationState(TypedDict, total=False):
    """Current request identity and public projection. Reset only at receive."""

    agent_runtime: str
    task_id: str
    public_run_id: str
    agent_run_id: str
    project_memory_result: dict | None
    public_events: list[dict]
    routing_result: dict
    final_response: dict | None


class PlanningReviewState(TypedDict, total=False):
    """Candidates, HITL revisions and frozen approval remain until the next new request."""

    reviews: list[dict]
    plan_views: list[dict]
    asset_revision: str
    interaction_id: str
    interaction_revision: int
    interaction_data: dict | None
    review_action: dict | None
    review_error: str | None
    approved_snapshot: dict | None
    planning_revision_count: int
    planning_feedback: list[dict]
    planning_previous_reviews: list[dict]
    planning_question: str | None
    planning_validation_error: str | None
    planning_route: str
    planning_activity_id: str


class ExecutionState(TypedDict, total=False):
    """Exact outbound payload, operation progress and evidence; retain for resume/recovery."""

    dataset_output_dir: str
    execution_id: str | None
    executor_version: int
    executor_operation_number: int
    executor_operation_id: str
    executor_wait_phase: str
    execution_command: dict
    execution_phase: str
    submitted_steps: list[dict]
    next_step_sequence: int
    completed_steps: list[str]
    skipped_steps: list[str]
    execution_decisions: dict
    pending_decisions: list[dict]
    decision_review: dict | None
    execution_review_validation_error: str | None
    observations: list[dict]
    execution_status: str
    execution_error: dict | str | None
    analysis_failure: bool
    terminal_event_seen: bool
    report_status: str


class RepairState(TypedDict, total=False):
    """Accepted/candidate repair and policy evidence; never clear on a user/Event resume."""

    execution_snapshot: dict | None
    failed_step_ids: list[str]
    repair_attempts: int
    repair_max_attempts: int
    repair_authorized_level: int
    repair_candidate: dict | None
    repair_review: dict | None
    repair_action: str
    repair_history: list[dict]
    repair_stop_reason: str | None
    repair_validation_error: str | None


class DeliveryState(TypedDict, total=False):
    """Durable consumption receipts. Retain through projection/replay; reset for a new Run."""

    initial_request_receipt: dict | None
    user_resume_receipt: dict | None
    ew_pending: dict
    ew_receipts: dict
    ew_sequences: dict


class PlanningState(OwnerState, RequestState, SessionState, PresentationState, PlanningReviewState, ExecutionState, RepairState, DeliveryState, total=False):
    """All 77 existing channels. Last-value semantics and node names are unchanged."""


_CHANNEL_TYPES = get_type_hints(PlanningState)


def _input(name: str, fields: str):
    """Derive each input type from the one authoritative channel declaration."""
    return TypedDict(name, {key: _CHANNEL_TYPES[key] for key in fields.split()}, total=False)


_EVENT = 'session_id public_run_id agent_run_id'
_ROLE = 'user_id project_id session_id run_id user_request model_selection project_system_prompt project_prompt_version'
_SNAPSHOT = 'approved_snapshot execution_snapshot repair_history'
_REPAIR = _SNAPSHOT + ' completed_steps skipped_steps execution_decisions observations failed_step_ids repair_attempts repair_max_attempts repair_authorized_level'
_BOUNDARY = 'task_id execution_id ew_pending ew_receipts ew_sequences'


# Schemas describe reads including delegated helpers. Nodes may return partial
# updates to any declared channel; branching still sees the full durable state.
NODE_INPUTS = {
    'receive': _input('ReceiveInput', 'user_id project_id session_id run_id user_request history last_analysis_context kernel_profile initial_request_identity'),
    'conversation': _input('ConversationInput', _EVENT + ' ' + _ROLE + ' history last_analysis_context public_events'),
    'publish_review': _input('PublishReviewInput', _EVENT + ' reviews interaction_id interaction_revision planning_question planning_revision_count review_error user_resume_receipt public_events'),
    'await_review': _input('AwaitReviewInput', 'interaction_data'),
    'apply_review': _input('ApplyReviewInput', _EVENT + ' user_id project_id kernel_profile dataset_output_dir asset_revision reviews review_action interaction_data interaction_id interaction_revision planning_revision_count planning_feedback planning_previous_reviews history user_resume_receipt'),
    'revise_plan': _input('RevisePlanInput', _EVENT + ' ' + _ROLE + ' kernel_profile dataset_output_dir history last_analysis_context reviews planning_previous_reviews planning_feedback planning_revision_count planning_activity_id interaction_id interaction_revision public_events'),
    'execution_select': _input('SelectInput', _SNAPSHOT + ' completed_steps skipped_steps execution_decisions observations execution_id executor_operation_number executor_version next_step_sequence repair_attempts task_id'),
    'execution_submit': _input('SubmitInput', _EVENT + ' execution_id execution_command submitted_steps executor_operation_number repair_history public_events'),
    'execution_register': _input('RegisterInput', 'task_id execution_id'),
    'execution_wait': _input('WaitExecutorInput', 'task_id execution_id'),
    'execution_receipt': _input('ReceiptInput', _BOUNDARY),
    'execution_process_event': _input('ProcessEventInput', _EVENT + ' ' + _SNAPSHOT + ' ew_pending executor_operation_id executor_operation_number submitted_steps completed_steps skipped_steps execution_decisions observations repair_attempts repair_authorized_level repair_max_attempts repair_review repair_stop_reason'),
    'execution_review': _input('ExecutionReviewInput', _EVENT + ' ' + _ROLE + ' ' + _SNAPSHOT + ' observations completed_steps execution_decisions pending_decisions public_events'),
    'execution_decision_wait': _input('DecisionWaitInput', _BOUNDARY + ' decision_review execution_decisions'),
    'execution_decision_applied': _input('DecisionAppliedInput', _EVENT + ' execution_phase user_resume_receipt decision_review execution_decisions'),
    'execution_finalize': _input('FinalizeInput', _EVENT + ' ' + _SNAPSHOT + ' execution_id executor_version public_events'),
    'execution_cancel': _input('CancelInput', _SNAPSHOT + ' execution_id'),
    'execution_report': _input('ReportInput', _EVENT + ' ' + _ROLE + ' ' + _SNAPSHOT + ' terminal_event_seen observations execution_status analysis_failure execution_id execution_decisions skipped_steps planning_revision_count repair_attempts repair_max_attempts repair_authorized_level repair_stop_reason public_events'),
    'execution_repair_propose': _input('RepairProposeInput', _EVENT + ' ' + _ROLE + ' ' + _REPAIR + ' executor_operation_number execution_error public_events'),
    'execution_repair_wait': _input('RepairWaitInput', _BOUNDARY + ' ' + _REPAIR + ' repair_candidate repair_review'),
    'execution_repair_applied': _input('RepairAppliedInput', _EVENT + ' execution_phase user_resume_receipt repair_review repair_action'),
    'execution_repair_apply': _input('RepairApplyInput', _EVENT + ' ' + _REPAIR + ' repair_candidate executor_operation_number public_events'),
    'execution_repair_reject': _input('RepairRejectInput', _REPAIR + ' repair_candidate'),
}
