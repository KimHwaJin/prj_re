"""Factories and dependency container for graph-level agent injection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_config import AgentSettings
from agent_service.agents.analysis.components.specs import COMPONENT_SPECS, ComponentType
from agent_service.agents.analysis.components.workflow_generator import (
    create_workflow_generator_agent,
)
from agent_service.agents.analysis.components.report_writer import (
    create_report_generator_agent,
)
from agent_service.agents.analysis.prompts.conditional_decision_prompt import (
    CONDITIONAL_DECISION_PROMPT,
)
from agent_service.agents.analysis.prompts.skill_selector_prompt import (
    SKILL_SELECTOR_PROMPT,
)
from agent_service.agents.analysis.tools.catalog import (
    load_workflow_catalog_context,
)
from agent_service.agents.analysis.schemas.agents.workflow_generator_schema import SkillSelectionOutput
from agent_service.agents.analysis.schemas.agents.orchestration_schema import ConditionalDecisionOutput
from agent_service.agents.analysis.workflow.workflow_recommender import WorkflowRecommender
from agent_service.agents.analysis.components.interfaces import (
    InvokableAgent,
    JsonMessageAgentAdapter,
    LabelOnlyLLMAgent,
    PlaceholderAgent,
    SimpleLLMAgent,
    StructuredLLMAgent,
)


@dataclass(frozen=True)
class AgentDependencies:
    routing_agent: InvokableAgent
    analysis_intent_agent: InvokableAgent
    workflow_recommender: InvokableAgent
    workflow_agent: InvokableAgent
    faq_agent: InvokableAgent
    file_lookup_agent: InvokableAgent
    report_agent: InvokableAgent | None = None
    conditional_decision_agent: InvokableAgent | None = None
    skill_selector_agent: InvokableAgent | None = None


def create_chat_model(settings: AgentSettings) -> Any:
    """Create either the local Azure model or internal OpenAI-compatible model."""
    if settings.model_provider == "azure_openai":
        from langchain_openai import AzureChatOpenAI

        if not settings.azure_openai_api_key or not settings.azure_openai_endpoint:
            raise ValueError(
                "Azure testing requires AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT"
            )
        return AzureChatOpenAI(
            api_key=settings.azure_openai_api_key,
            azure_endpoint=settings.azure_openai_endpoint,
            api_version=settings.azure_openai_api_version,
            azure_deployment=settings.azure_openai_deployment or settings.model_name,
            temperature=settings.model_temperature,
            timeout=settings.model_timeout_seconds,
            max_retries=settings.model_max_retries,
        )

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
    routing_spec = COMPONENT_SPECS[ComponentType.ROUTING_AGENT]
    analysis_intent_spec = COMPONENT_SPECS[
        ComponentType.ANALYSIS_INTENT_CLASSIFIER
    ]
    return AgentDependencies(
        routing_agent=LabelOnlyLLMAgent(
            model=model,
            system_prompt=routing_spec.system_prompt,
            output_type=routing_spec.response_format,
            label_field="route",
            allowed_labels=(
                "analysis",
                "faq",
                "file_lookup",
                "revise_workflow",
                "reselect_data",
                "cancel",
            ),
        ),
        analysis_intent_agent=LabelOnlyLLMAgent(
            model=model,
            system_prompt=analysis_intent_spec.system_prompt,
            output_type=analysis_intent_spec.response_format,
            label_field="intent",
            allowed_labels=(
                "failure_prediction",
                "root_cause",
                "data_drift",
            ),
        ),
        workflow_recommender=WorkflowRecommender(),
        skill_selector_agent=StructuredLLMAgent(
            model=model,
            system_prompt=(
                f"{SKILL_SELECTOR_PROMPT}\n\n{load_workflow_catalog_context()}"
            ),
            output_type=SkillSelectionOutput,
            method=settings.model_structured_output_mode,
        ),
        workflow_agent=JsonMessageAgentAdapter(
            create_workflow_generator_agent(
                model,
                structured_output_mode=(
                    settings.model_structured_output_mode
                ),
            )
        ),
        faq_agent=SimpleLLMAgent(
            model=model,
            system_prompt="사용자의 FAQ 질문에 간결하고 정확하게 답변하세요.",
        ),
        file_lookup_agent=PlaceholderAgent(
             "파일 조회는 현재 지원되지 않습니다."
        ),
        report_agent=create_report_generator_agent(model),
        conditional_decision_agent=StructuredLLMAgent(
            model=model,
            system_prompt=CONDITIONAL_DECISION_PROMPT,
            output_type=ConditionalDecisionOutput,
            method=settings.model_structured_output_mode,
        ),
    )
