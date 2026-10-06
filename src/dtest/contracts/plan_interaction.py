"""Typed public plan forms and resume commands, shared by API and Agent."""

from typing import Any, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ParameterChange(StrictModel):
    step_id: str
    parameter: str
    value: Any


class ExecutionOverrides(StrictModel):
    mode: Literal["SINGLE", "MULTI"] | None = None
    repair_level: int | None = Field(default=None, ge=0, le=4)
    max_repair_attempts: int | None = Field(default=None, ge=0)


class PlanAction(StrictModel):
    action: Literal["edit_plan", "approve_plan"]
    plan_id: str
    plan_revision: int = Field(ge=1, strict=True)
    input_values: dict[str, Any] = Field(default_factory=dict)
    step_changes: list[ParameterChange] = Field(default_factory=list)
    excluded_step_ids: list[str] = Field(default_factory=list)
    execution_overrides: ExecutionOverrides = Field(
        default_factory=ExecutionOverrides
    )


class ResumeCommand(StrictModel):
    resume: "PlanAction | DecisionAction | RepairAction | PlanRevisionAction"


class DecisionAction(StrictModel):
    action: Literal["approve_decisions"]
    interaction_id: UUID
    revision: int = Field(ge=1, strict=True)
    values: dict[str, Any]


class RepairAction(StrictModel):
    action: Literal["approve_repair", "reject_repair"]
    interaction_id: UUID
    revision: int = Field(ge=1, strict=True)
    proposal_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    allow_policy_escalation: bool = Field(default=False, strict=True)


class PlanRevisionAction(StrictModel):
    action: Literal["replan", "answer_clarification"]
    interaction_id: UUID
    revision: int = Field(ge=1, strict=True)
    feedback: str = Field(min_length=1, max_length=4000)

    @field_validator("feedback")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Feedback must contain text")
        return value.strip()


ResumeCommand.model_rebuild()


class ResumeRequest(StrictModel):
    run_id: UUID
    resume_token: UUID
    command: ResumeCommand


class InputView(StrictModel):
    name: str
    title: str
    description: str
    kind: Literal["parameter", "data_reference"]
    required: bool
    editable: bool
    value_schema: dict | bool
    origin: Literal["agent", "workflow_default", "user", "unresolved"]
    has_value: bool
    value: Any = None


class ParameterView(StrictModel):
    name: str
    title: str | None = None
    description: str | None = None
    has_value: bool = False
    origin: Literal["agent", "tool_default", "user", "unresolved"] = (
        "unresolved"
    )
    kind: Literal[
        "workflow_input",
        "literal",
        "step_reference",
        "deferred",
        "system_context",
    ]
    editable: bool
    value: Any = None
    value_schema: dict | bool | None = None
    input_name: str | None = None
    step_id: str | None = None
    selector: list[str | int] | None = None
    decision_id: str | None = None
    guidance: str | None = None
    context_key: str | None = None


class StepView(StrictModel):
    step_id: str
    skill_id: str
    tool_id: str
    function_name: str
    description: str
    depends_on: list[str]
    parameters: list[ParameterView]
    when: dict | None = None
    status: Literal["planned", "excluded"]


class DecisionView(StrictModel):
    decision_id: str
    evidence_steps: list[str]
    guidance: str
    value_schema: dict | bool
    status: Literal["deferred"] = "deferred"


class OutputView(StrictModel):
    output_id: str
    kind: str
    description: str
    status: Literal["planned", "excluded_by_user"]


class PolicyView(StrictModel):
    mode: Literal["SINGLE", "MULTI"]
    repair_level: int
    max_repair_attempts: int
    review_mode: str
    review_interval_tools: int | None = None
    allowed_modes: list[Literal["SINGLE", "MULTI"]]
    repair_level_limit: int
    max_repair_attempts_limit: int


class SkillView(StrictModel):
    skill_id: str
    name: str
    description: str


class WorkflowCatalogReference(StrictModel):
    workflow_id: str
    content_sha256: str
    resource_revision: int
    search_revision: int
    similarity: float


class PlanView(StrictModel):
    catalog_reference: WorkflowCatalogReference | None = None
    plan_id: str
    plan_revision: int
    workflow_id: str
    execution_kind: Literal["registered", "free_code"] = "registered"
    workflow_eligible: bool = True
    approval_mode: Literal["user", "configuration"] = "user"
    definition_version: int
    name: str
    goal: str
    skills: list[SkillView]
    inputs: list[InputView]
    steps: list[StepView]
    decisions: list[DecisionView]
    execution: PolicyView
    outputs: list[OutputView]


class PlanningRevisionPolicy(StrictModel):
    used: int = Field(ge=0)
    limit: int = Field(ge=1, le=20)
    free_code_allowed: bool
    free_code_require_approval: bool


class ReviewPayload(StrictModel):
    plans: list[PlanView] = Field(min_length=1)
    revision_policy: PlanningRevisionPolicy | None = None
    notices: list[str] = Field(default_factory=list)


class InteractionData(StrictModel):
    interaction_id: UUID
    revision: int = Field(ge=1, strict=True)
    kind: Literal["plan_review"]
    status: Literal["open"]
    resume_token: UUID
    summary: str
    payload: ReviewPayload


class InteractionEvent(StrictModel):
    schema_version: Literal[1] = 1
    type: Literal["interaction.opened", "interaction.updated"]
    sequence: int = Field(ge=1, strict=True)
    session_id: UUID
    run_id: UUID
    occurred_at: str
    data: InteractionData


class ResolutionPayload(StrictModel):
    approved_plan: PlanView
    notices: list[str] = Field(default_factory=list)


class ResolutionData(StrictModel):
    interaction_id: UUID
    revision: int = Field(ge=1, strict=True)
    kind: Literal["plan_review"]
    status: Literal["resolved"]
    resolution: Literal["approved", "auto_approved"]
    payload: ResolutionPayload


class InteractionResolvedEvent(StrictModel):
    schema_version: Literal[1] = 1
    type: Literal["interaction.resolved"]
    sequence: int = Field(ge=1, strict=True)
    session_id: UUID
    run_id: UUID
    occurred_at: str
    data: ResolutionData


class DecisionField(StrictModel):
    decision_id: str
    guidance: str
    evidence_steps: list[str]
    value_schema: dict | bool
    has_value: bool
    value: Any = None


class DecisionPayload(StrictModel):
    decisions: list[DecisionField] = Field(min_length=1, max_length=100)


class DecisionInteractionData(StrictModel):
    interaction_id: UUID
    revision: int = Field(ge=1, strict=True)
    kind: Literal["decision_review"]
    status: Literal["open"]
    resume_token: UUID
    summary: str
    payload: DecisionPayload


class DecisionInteractionEvent(StrictModel):
    schema_version: Literal[1] = 1
    type: Literal["interaction.opened"]
    sequence: int = Field(ge=1, strict=True)
    session_id: UUID
    run_id: UUID
    occurred_at: str
    data: DecisionInteractionData


class ClarificationPayload(StrictModel):
    revision_policy: PlanningRevisionPolicy | None = None
    question: str = Field(min_length=1, max_length=12000)
    notices: list[str] = Field(default_factory=list)


class ClarificationData(StrictModel):
    interaction_id: UUID
    revision: int = Field(ge=1, strict=True)
    kind: Literal["planning_question"]
    status: Literal["open"]
    resume_token: UUID
    summary: str
    payload: ClarificationPayload


class ClarificationEvent(StrictModel):
    schema_version: Literal[1] = 1
    type: Literal["interaction.opened", "interaction.updated"]
    sequence: int = Field(ge=1, strict=True)
    session_id: UUID
    run_id: UUID
    occurred_at: str
    data: ClarificationData


def validate_plan_revision(interaction, raw, *, count, limit):
    action = PlanRevisionAction.model_validate(raw)
    if str(action.interaction_id) != interaction.get(
        "interaction_id"
    ) or action.revision != interaction.get("revision"):
        raise ValueError("Stale planning interaction; refresh Run state")
    expected = (
        "answer_clarification"
        if interaction.get("kind") == "planning_question"
        else "replan"
    )
    if (
        interaction.get("status") != "open"
        or interaction.get("kind") not in {"plan_review", "planning_question"}
        or action.action != expected
    ):
        raise ValueError(
            "This interaction does not accept that planning action"
        )
    if count >= limit:
        raise ValueError(
            "Planning revision limit reached; approve, cancel, or "
            "start a new "
            "Run"
        )
    return action


class PlanningTransitionPayload(StrictModel):
    notices: list[str] = Field(default_factory=list)


class PlanningTransitionData(StrictModel):
    interaction_id: UUID
    revision: int = Field(ge=1, strict=True)
    kind: Literal["plan_review", "planning_question"]
    status: Literal["resolved"]
    resolution: Literal["replanning", "answered"]
    payload: PlanningTransitionPayload


class PlanningTransitionEvent(StrictModel):
    schema_version: Literal[1] = 1
    type: Literal["interaction.resolved"]
    sequence: int = Field(ge=1, strict=True)
    session_id: UUID
    run_id: UUID
    occurred_at: str
    data: PlanningTransitionData
