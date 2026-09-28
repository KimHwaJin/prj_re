"""Recommend a stored Workflow through an injected search service."""

from __future__ import annotations

from agent_config import AgentSettings
from agent_service.agents.analysis.message_utils import as_message_content
from agent_service.agents.analysis.dependencies import AgentDependencies
from agent_service.agents.analysis.state import AnalysisWorkflowState
from agent_service.agents.analysis.schemas.agents.workflow_recommender_schema import (
    WorkflowRecommendationOutput,
)


def make_recommend_workflow(
    deps: AgentDependencies,
    settings: AgentSettings,
):
    async def recommend_workflow(state: AnalysisWorkflowState) -> dict:
        request = {
            "user_request": state["user_request"],
            "analysis_intent": state["analysis_intent"],
            "analysis_context": state["analysis_context"],
            "data_selection": state["data_selection"],
        }
        raw_recommendation = (
            await deps.workflow_recommender.ainvoke(request)
            if settings.workflow_recommendation_enabled
            else None
        )
        if raw_recommendation:
            output = WorkflowRecommendationOutput.model_validate(
                raw_recommendation
            )
        else:
            output = WorkflowRecommendationOutput(
                recommendation_available=False,
                recommendation=None,
                no_match_reason=(
                    "조건에 맞는 추천 워크플로우 후보를 찾지 못했습니다."
                ),
            )
        payload = output.model_dump(mode="json")
        candidates = list(state.get("workflow_candidates", []))
        if output.recommendation_available:
            recommendation = payload["recommendation"]
            workflow = recommendation.pop("workflow")
            candidates.append(
                {
                    "id": "recommended_1",
                    "origin": "recommended",
                    "status": workflow["workflow"]["status"],
                    "workflow": workflow,
                    "metadata": recommendation,
                }
            )
        # TODO(CRUD): 에이전트 메시지 POST
        return {
            "recommendation": payload,
            "workflow_candidates": candidates,
            "messages": [
                {
                    "role": "assistant",
                    "name": "workflow_recommender",
                    "content": as_message_content(
                        {
                            "status": "candidate_search_complete",
                            "result": payload,
                        }
                    ),
                }
            ],
        }

    return recommend_workflow
