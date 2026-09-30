"""Typed public plan forms and resume commands, shared by API and Agent."""
from typing import Any, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class ParameterChange(StrictModel):
    step_id: str
    parameter: str
    value: Any


class ExecutionOverrides(StrictModel):
    mode: Literal['SINGLE', 'MULTI'] | None = None
    repair_level: int | None = Field(default=None, ge=0, le=4)
    max_repair_attempts: int | None = Field(default=None, ge=0)


class PlanAction(StrictModel):
    action: Literal['edit_plan', 'approve_plan']
    plan_id: str
    plan_revision: int = Field(ge=1, strict=True)
    input_values: dict[str, Any] = Field(default_factory=dict)
    step_changes: list[ParameterChange] = Field(default_factory=list)
    excluded_step_ids: list[str] = Field(default_factory=list)
    execution_overrides: ExecutionOverrides = Field(default_factory=ExecutionOverrides)


class ResumeCommand(StrictModel):
    resume: PlanAction


class ResumeRequest(StrictModel):
    run_id: UUID
    resume_token: UUID
    command: ResumeCommand


class InputView(StrictModel):
    name: str
    title: str
    description: str
    kind: Literal['parameter', 'data_reference']
    required: bool
    editable: bool
    value_schema: dict | bool
    origin: Literal['agent', 'workflow_default', 'user', 'unresolved']
    has_value: bool
    value: Any = None


class ParameterView(StrictModel):
    name: str
    kind: Literal['workflow_input', 'literal', 'step_reference', 'deferred', 'system_context']
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
    status: Literal['planned', 'excluded']


class DecisionView(StrictModel):
    decision_id: str
    evidence_steps: list[str]
    guidance: str
    value_schema: dict | bool
    status: Literal['deferred'] = 'deferred'


class OutputView(StrictModel):
    output_id: str
    kind: str
    description: str
    status: Literal['planned', 'excluded_by_user']


class PolicyView(StrictModel):
    mode: Literal['SINGLE', 'MULTI']
    repair_level: int
    max_repair_attempts: int
    review_mode: str
    review_interval_tools: int | None = None
    allowed_modes: list[Literal['SINGLE', 'MULTI']]
    repair_level_limit: int
    max_repair_attempts_limit: int


class SkillView(StrictModel):
    skill_id: str
    name: str
    description: str


class PlanView(StrictModel):
    plan_id: str
    plan_revision: int
    workflow_id: str
    definition_version: int
    name: str
    goal: str
    skills: list[SkillView]
    inputs: list[InputView]
    steps: list[StepView]
    decisions: list[DecisionView]
    execution: PolicyView
    outputs: list[OutputView]


class ReviewPayload(StrictModel):
    plans: list[PlanView] = Field(min_length=1)
    notices: list[str] = Field(default_factory=list)


class InteractionData(StrictModel):
    interaction_id: UUID
    revision: int = Field(ge=1, strict=True)
    kind: Literal['plan_review']
    status: Literal['open']
    resume_token: UUID
    summary: str
    payload: ReviewPayload


class InteractionEvent(StrictModel):
    schema_version: Literal[1] = 1
    type: Literal['interaction.opened', 'interaction.updated']
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
    kind: Literal['plan_review']
    status: Literal['resolved']
    resolution: Literal['approved']
    payload: ResolutionPayload


class InteractionResolvedEvent(StrictModel):
    schema_version: Literal[1] = 1
    type: Literal['interaction.resolved']
    sequence: int = Field(ge=1, strict=True)
    session_id: UUID
    run_id: UUID
    occurred_at: str
    data: ResolutionData
