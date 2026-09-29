"""The only place that imports and builds the receiving Agent's graph."""

from __future__ import annotations

from typing import Any

from agent_config import load_agent_settings
from app.agent_worker.graph_boundary import ExecutionBindings
from agent_service.agents.analysis.dependencies import create_llm_dependencies
from agent_service.agents.analysis.graph import (
    build_analysis_workflow_graph,
)
from app.services.workflow_persistence import workflow_store_from_environment


def build_agent_graph(
    *,
    bindings: ExecutionBindings,
    checkpointer: Any,
    executor_client: Any = None,
) -> Any:
    """Compile the service graph with Worker bindings and its checkpointer."""

    settings = load_agent_settings()
    dependencies = create_llm_dependencies(settings)
    workflow_store = workflow_store_from_environment()
    return build_analysis_workflow_graph(
        dependencies,
        settings,
        bindings=bindings,
        checkpointer=checkpointer,
        executor_client=executor_client,
        workflow_store=workflow_store,
    )
