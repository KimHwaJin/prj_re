from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from agent_service.agents.analysis.prompts.intent_classifier_prompt import ANALYSIS_INTENT_PROMPT
from agent_service.agents.analysis.prompts.routing_agent_prompt import ROUTING_AGENT_PROMPT
from agent_service.agents.analysis.prompts.workflow_generator_prompt import WORKFLOW_GENERATOR_PROMPT
from agent_service.agents.analysis.tools.catalog import read_skill_documents
from agent_service.agents.analysis.schemas.agents.orchestration_schema import (
    AnalysisIntentOutput,
    RoutingOutput,
)
from agent_service.agents.analysis.schemas.workflows.workflow_plan_format import WorkflowPlanOutput


class ComponentType(StrEnum):
    ROUTING_AGENT = "ROUTING_AGENT"
    ANALYSIS_INTENT_CLASSIFIER = "ANALYSIS_INTENT_CLASSIFIER"
    WORKFLOW_GENERATOR = "WORKFLOW_GENERATOR"
    REPORT_WRITER = "REPORT_WRITER"

@dataclass(frozen=True)
class AgentCreateSpec:
    name: str
    system_prompt: str
    response_format: Any
    tools: tuple[Any, ...] = ()


COMPONENT_SPECS: dict[ComponentType, AgentCreateSpec] = {
    ComponentType.ROUTING_AGENT: AgentCreateSpec(
        name="routing_agent",
        system_prompt=ROUTING_AGENT_PROMPT,
        response_format=RoutingOutput,
    ),
    ComponentType.ANALYSIS_INTENT_CLASSIFIER: AgentCreateSpec(
        name="analysis_intent_classifier_agent",
        system_prompt=ANALYSIS_INTENT_PROMPT,
        response_format=AnalysisIntentOutput,
    ),
    ComponentType.WORKFLOW_GENERATOR: AgentCreateSpec(
        name="workflow_generator_agent",
        system_prompt=WORKFLOW_GENERATOR_PROMPT,
        response_format=WorkflowPlanOutput,
        tools=(read_skill_documents,),
    ),
}
