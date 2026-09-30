"""The only place that imports and builds the receiving Agent's graph."""

from __future__ import annotations

from typing import Any

from agent_config import load_agent_settings
from service_contracts.executor_boundary import ExecutionBindings
from agent_service.agents.analysis.planning.runtime import PlanningRuntime
from agent_service.agents.analysis.planning.graph import build_planning_graph


def build_agent_graph(
    *,
    bindings: ExecutionBindings,
    checkpointer: Any,
    executor_client: Any = None,
) -> Any:
    """Compile the service graph with Worker bindings and its checkpointer."""

    settings = load_agent_settings()
    runtime=PlanningRuntime(settings,executor=executor_client,bindings=bindings)
    return build_planning_graph(runtime,checkpointer=checkpointer)
