"""Input contract for the Workflow Generator agent."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .orchestration_schema import AnalysisContextResponse, DataSelectionResponse


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SkillSelectionOutput(StrictModel):
    """Complete set of Skill names selected before resource loading."""

    skill_names: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_skill_names(self) -> "SkillSelectionOutput":
        if len(self.skill_names) != len(set(self.skill_names)):
            raise ValueError("skill_names must not contain duplicates")
        return self


class WorkflowGenerationRequest(StrictModel):
    """Confirmed graph state passed to the Workflow Generator."""

    user_request: str = Field(min_length=1)
    analysis_intent: Literal["root_cause", "failure_prediction", "data_drift"]
    analysis_context: AnalysisContextResponse
    workflow_context: dict[str, Any] = Field(default_factory=dict)
    data_selection: DataSelectionResponse
    generation_mode: Literal["new"] = "new"
    additional_information: dict[str, Any] = Field(default_factory=dict)
    rejection_feedback: str | None = None
    validation_feedback: str | None = None
    previous_workflow: dict[str, Any] | None = None


__all__ = ["SkillSelectionOutput", "WorkflowGenerationRequest"]
