"""Typed repair proposal/approval contracts. Public forms never contain Python source."""
from typing import Any, Literal
from uuid import UUID
from pydantic import Field
from service_contracts.plan_interaction import StrictModel, StepView, RepairAction
from service_contracts.plan_review import require


class ArgumentRepair(StrictModel):
    step_id: str
    arguments: dict[str, Any]


class SourceRepair(StrictModel):
    step_id: str
    code: str = Field(min_length=1, max_length=64000)


class RepairResponse(StrictModel):
    can_repair: bool
    summary: str = Field(min_length=1, max_length=4000)
    reason: str = Field(min_length=1, max_length=6000)
    evidence_steps: list[str] = Field(max_length=100)
    argument_changes: list[ArgumentRepair] = Field(default_factory=list, max_length=100)
    source_changes: list[SourceRepair] = Field(default_factory=list, max_length=100)
    replacement_steps: list[dict] | None = Field(default=None, min_length=1, max_length=256)
    replacement_decisions: list[dict] | None = Field(default=None, max_length=100)
    needs_user_input: bool = False


class RepairPayload(StrictModel):
    proposal_sha256: str
    attempt: int
    max_attempts: int
    authorized_level: int
    required_level: int
    requires_policy_escalation: bool
    summary: str
    failed_step_ids: list[str]
    completed_step_ids: list[str]
    changed_step_ids: list[str]
    steps: list[StepView]
    source_modified: bool
    workflow_eligible: bool
    validation_scope: Literal['structure_and_bindings'] = 'structure_and_bindings'


class RepairInteractionData(StrictModel):
    interaction_id: UUID
    revision: int = Field(ge=1, strict=True)
    kind: Literal['repair_review']
    status: Literal['open']
    resume_token: UUID
    summary: str
    payload: RepairPayload


class RepairInteractionEvent(StrictModel):
    schema_version: Literal[1] = 1
    type: Literal['interaction.opened']
    sequence: int = Field(ge=1, strict=True)
    session_id: UUID
    run_id: UUID
    occurred_at: str
    data: RepairInteractionData


def validate_repair_action(review, raw):
    action = RepairAction.model_validate(raw)
    require(review['status']=='open', 'Repair proposal is no longer open')
    require(str(action.interaction_id)==review['interaction_id'] and action.revision==review['revision'], 'Stale repair form')
    require(action.proposal_sha256==review['payload']['proposal_sha256'], 'Repair proposal differs from the reviewed version')
    require(action.action=='reject_repair' or not review['payload']['requires_policy_escalation'] or action.allow_policy_escalation,
            'Explicit policy escalation approval is required')
    return action
