"""Deterministic LLM substitutes for the service load scenarios through Executor submission.

Only model-backed invokables are replaced. Graph nodes, compilation, HITL,
message persistence, workflow catalog, queue and checkpointers remain real.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import time
from typing import Any, Callable

from agent_config import AgentSettings
from app.services.workflow_recommender import WorkflowRecommender
from .dependencies import AgentDependencies


@dataclass(frozen=True)
class ScriptedAgent:
    respond: Callable[[dict[str, Any]], dict[str, Any]]
    delay_ms: int

    def invoke(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Graph runs synchronous nodes in its executor; this does not sleep the
        # HTTP event loop. Optional latency also exposes executor/thread limits.
        if self.delay_ms:
            time.sleep(self.delay_ms / 1000)
        return deepcopy(self.respond(payload))


def workflow_plan(payload: dict[str, Any]) -> dict[str, Any]:
    """A schema-valid plan, compiled and persisted by the ordinary graph nodes."""
    return {
        "plan_version": "1.0",
        "workflow": {
            "id": "service_load_profile",
            "name": "Service load test profile",
            "description": "Deterministic mock LLM output for service testing.",
            "goal": payload["user_request"],
            "status": "ready",
            "context": payload.get("workflow_context", {}),
            "steps": [{
                "id": "profile_selected_data",
                "skill": "data_quality_check",
                "depends_on": ["load_data_1"],
                "tools": [{
                    "tool": name,
                    "selection_reason": "Deterministic service load scenario.",
                    "arguments": {"data": {"source": "step_output", "step_id": "load_data_1", "output": "data"}},
                } for name in ("profile_data", "compute_statistics", "detect_outliers")],
            }],
            "outputs": {"profile": {"step_id": "profile_selected_data", "tool": "profile_data", "output": "profile"}},
        },
    }


def outside_scenario(payload: dict[str, Any]) -> dict[str, Any]:
    raise RuntimeError("Mock LLM supports only analysis through Executor submission; result/report processing is unsupported")


def create_mock_dependencies(settings: AgentSettings) -> AgentDependencies:
    def agent(fn):
        return ScriptedAgent(fn, settings.model_mock_delay_ms)

    return AgentDependencies(
        routing_agent=agent(lambda _: {"route": "analysis", "reason": "service load scenario"}),
        analysis_intent_agent=agent(lambda _: {"intent": "failure_prediction", "reason": "service load scenario"}),
        # This is the same current recommendation implementation used in real mode.
        workflow_recommender=WorkflowRecommender(),
        skill_selector_agent=agent(lambda _: {"skill_names": ["data_quality_check"]}),
        workflow_agent=agent(workflow_plan),
        faq_agent=agent(outside_scenario),
        file_lookup_agent=agent(outside_scenario),
        report_agent=agent(outside_scenario),
        conditional_decision_agent=agent(outside_scenario),
    )
