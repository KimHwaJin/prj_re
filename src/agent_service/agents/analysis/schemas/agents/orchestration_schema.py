"""Structured contracts for the user-agent orchestration graph."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RoutingOutput(StrictModel):
    route: Literal[
        "analysis",
        "faq",
        "file_lookup",
        "revise_workflow",
        "reselect_data",
        "cancel",
    ]
    reason: str = Field(min_length=1)


class AnalysisIntentOutput(StrictModel):
    intent: Literal["root_cause", "failure_prediction", "data_drift"]
    reason: str = Field(min_length=1)


class RequestContext(StrictModel):
    user_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)


class AnalysisContextResponse(StrictModel):
    objective: str = Field(min_length=1)


class SelectedDataset(StrictModel):
    role: Literal["x", "y"]
    data_type: Literal["nce", "wt_symbol"]
    lot_cd: str = Field(min_length=1)
    process: list[str] = Field(min_length=1)
    query_mode: str = Field(min_length=1)
    start_dt: date
    end_dt: date
    limit: int = Field(gt=0)
    transform_op: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_dataset(self) -> "SelectedDataset":
        if self.end_dt < self.start_dt:
            raise ValueError("end_dt must be on or after start_dt")
        expected_transform = {
            "nce": "pivot",
            "wt_symbol": "wt_fail_pivot",
        }.get(self.data_type.lower())
        if expected_transform and self.transform_op != expected_transform:
            raise ValueError(
                f"{self.data_type} transform_op must be {expected_transform}"
            )
        return self


class DataSelectionResponse(StrictModel):
    data_count: int = Field(gt=0)
    datasets: list[SelectedDataset] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_selection(self) -> "DataSelectionResponse":
        if self.data_count != len(self.datasets):
            raise ValueError("data_count must equal the number of datasets")
        if {dataset.role for dataset in self.datasets} != {"x", "y"}:
            raise ValueError("analysis requires at least one X and one Y dataset")
        return self


class NextUserRequestResponse(StrictModel):
    action: Literal["continue", "cancel"]
    user_request: str | None = None

    @model_validator(mode="after")
    def validate_continue(self) -> "NextUserRequestResponse":
        if self.action == "continue" and not (self.user_request or "").strip():
            raise ValueError("user_request is required when action=continue")
        return self


class AdditionalInformationResponse(StrictModel):
    answers: dict[str, Any] = Field(min_length=1)


class WorkflowCandidateSelectionResponse(StrictModel):
    approved: bool = True
    selected_candidate_id: str | None = None
    feedback: str | None = None

    @model_validator(mode="after")
    def validate_selection(self) -> "WorkflowCandidateSelectionResponse":
        if self.approved and not (self.selected_candidate_id or "").strip():
            raise ValueError(
                "selected_candidate_id is required when approved=true"
            )
        if not self.approved and not (self.feedback or "").strip():
            raise ValueError("feedback is required when candidates are rejected")
        return self


class ApprovalDecision(StrictModel):
    approved: bool
    feedback: str | None = None

    @model_validator(mode="after")
    def validate_rejection(self) -> "ApprovalDecision":
        if not self.approved and not (self.feedback or "").strip():
            raise ValueError("feedback is required when a workflow is rejected")
        return self


class ConditionalToolDecision(StrictModel):
    tool_id: str = Field(min_length=1)
    decision: Literal["include", "exclude"]
    reason: str = Field(min_length=1)


class ConditionalDecisionOutput(StrictModel):
    decisions: list[ConditionalToolDecision] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_tools(self) -> "ConditionalDecisionOutput":
        tool_ids = [item.tool_id for item in self.decisions]
        if len(tool_ids) != len(set(tool_ids)):
            raise ValueError("conditional decision tool_id가 중복됩니다.")
        return self


class PlaceholderResponse(StrictModel):
    status: Literal["placeholder"] = "placeholder"
    message: str = Field(min_length=1)


class FaqOutput(StrictModel):
    answer: str = Field(min_length=1)


class NotebookCell(StrictModel):
    order: int = Field(gt=0)
    id: str = Field(min_length=1)
    cell_type: Literal["code"] = "code"
    role: Literal["tool_execution", "workflow_outputs"]
    skill: str | None = None
    code: str = Field(min_length=1)
    step_id: str | None = None
    tool_id: str | None = None
    tool: str | None = None

    @model_validator(mode="after")
    def validate_execution_metadata(self) -> "NotebookCell":
        if self.role == "tool_execution" and not all(
            (self.skill, self.step_id, self.tool_id, self.tool)
        ):
            raise ValueError(
                "tool_execution cell requires skill, step_id, tool_id, and tool"
            )
        return self


class NotebookGenerationOutput(StrictModel):
    message: str = Field(min_length=1)
    cells: list[NotebookCell] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_cells(self) -> "NotebookGenerationOutput":
        orders = [cell.order for cell in self.cells]
        if orders != list(range(1, len(self.cells) + 1)):
            raise ValueError("notebook cell order must be contiguous from 1")
        return self


__all__ = [
    "AdditionalInformationResponse",
    "AnalysisIntentOutput",
    "ApprovalDecision",
    "AnalysisContextResponse",
    "DataSelectionResponse",
    "FaqOutput",
    "NotebookGenerationOutput",
    "NextUserRequestResponse",
    "PlaceholderResponse",
    "RequestContext",
    "SelectedDataset",
    "WorkflowCandidateSelectionResponse",
]
