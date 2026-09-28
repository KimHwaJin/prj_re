"""Output contracts for stored Workflow recommendation."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkflowRecommendation(StrictModel):
    workflow_id: str = Field(min_length=1)
    similarity_score: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1)
    workflow: dict[str, Any]

    @model_validator(mode="after")
    def validate_workflow_json(self) -> "WorkflowRecommendation":
        if not isinstance(self.workflow.get("workflow"), dict):
            raise ValueError("workflow must contain a workflow JSON object")
        return self


class WorkflowRecommendationOutput(StrictModel):
    recommendation_available: bool
    recommendation: WorkflowRecommendation | None = None
    no_match_reason: str | None = None

    @model_validator(mode="after")
    def validate_recommendation(self) -> "WorkflowRecommendationOutput":
        if self.recommendation_available:
            if self.recommendation is None:
                raise ValueError("추천 가능하면 recommendation이 필요합니다.")
            if self.no_match_reason is not None:
                raise ValueError("추천 가능하면 no_match_reason을 지정할 수 없습니다.")
        else:
            if self.recommendation is not None:
                raise ValueError("추천 불가이면 recommendation을 지정할 수 없습니다.")
            if not self.no_match_reason:
                raise ValueError("추천 불가이면 no_match_reason이 필요합니다.")
        return self


__all__ = ["WorkflowRecommendation", "WorkflowRecommendationOutput"]
