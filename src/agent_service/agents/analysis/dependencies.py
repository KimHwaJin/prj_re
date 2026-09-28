"""Factories and dependency container for graph-level agent injection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_config import AgentSettings
from agent_service.agents.analysis.agent_builders import (
    conditional_decider,
    faq,
    intent_classifier,
    report_writer,
    routing,
    skill_selector,
    workflow_generator,
)
from agent_service.agents.analysis.workflow.workflow_recommender import WorkflowRecommender
from agent_service.agents.analysis.components.interfaces import AsyncInvokableAgent, PlaceholderAgent


@dataclass(frozen=True)
class AgentDependencies:
    routing_agent: AsyncInvokableAgent
    analysis_intent_agent: AsyncInvokableAgent
    workflow_recommender: AsyncInvokableAgent
    workflow_agent: AsyncInvokableAgent
    faq_agent: AsyncInvokableAgent
    file_lookup_agent: AsyncInvokableAgent
    report_agent: AsyncInvokableAgent | None = None
    conditional_decision_agent: AsyncInvokableAgent | None = None
    skill_selector_agent: AsyncInvokableAgent | None = None


def create_chat_model(settings: AgentSettings) -> Any:
    """Create the configured OpenAI-compatible model."""
    if settings.model_provider == "openai_compatible":
        from langchain_openai import ChatOpenAI

        if not settings.model_api_key or not settings.api_base_url:
            raise ValueError("Internal deployment requires MODEL_API_KEY and API_BASE_URL")
        model_kwargs: dict[str, Any] = {}
        if settings.model_enable_thinking is not None:
            model_kwargs["extra_body"] = {
                "chat_template_kwargs": {
                    "enable_thinking": settings.model_enable_thinking,
                }
            }
        return ChatOpenAI(
            api_key=settings.model_api_key,
            base_url=settings.api_base_url,
            model=settings.model_name,
            temperature=settings.model_temperature,
            timeout=settings.model_timeout_seconds,
            max_retries=settings.model_max_retries,
            **model_kwargs,
        )

    raise ValueError(f"Unsupported MODEL_PROVIDER: {settings.model_provider!r}")


def create_llm_dependencies(settings: AgentSettings) -> AgentDependencies:
    """Create registered agents and inject them into the graph boundary."""
    if settings.model_provider == "mock":
        from agent_service.agents.analysis.testing.mock_dependencies import create_mock_dependencies

        return create_mock_dependencies(settings)
    model = create_chat_model(settings)
    return AgentDependencies(
        routing_agent=routing.build_agent(model),
        analysis_intent_agent=intent_classifier.build_agent(model),
        workflow_recommender=WorkflowRecommender(),
        skill_selector_agent=skill_selector.build_agent(
            model, structured_output_mode=settings.model_structured_output_mode,
        ),
        workflow_agent=workflow_generator.build_agent(
            model, structured_output_mode=settings.model_structured_output_mode,
        ),
        faq_agent=faq.build_agent(model),
        file_lookup_agent=PlaceholderAgent("파일 조회는 현재 지원되지 않습니다."),
        report_agent=report_writer.build_agent(model),
        conditional_decision_agent=conditional_decider.build_agent(
            model, structured_output_mode=settings.model_structured_output_mode,
        ),
    )
