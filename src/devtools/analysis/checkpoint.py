"""Developer-only graph helpers for CLI and offline tests.

The service uses agent_service.runtime.langgraph.checkpointer instead.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from agent_config import AgentSettings
from agent_service.agents.analysis.dependencies import AgentDependencies
from agent_service.agents.analysis.graph import (
    build_analysis_workflow_graph,
)
from api_service.services.workflow_persistence import workflow_store_from_environment


@asynccontextmanager
async def compiled_postgres_graph(
    deps: AgentDependencies,
    settings: AgentSettings,
    *,
    submit_execution_start: Any | None = None,
    submit_execution_continue: Any | None = None,
    submit_execution_finish: Any | None = None,
    bindings: Any | None = None,
    execution_result_reader: Any | None = None,
) -> AsyncIterator[object]:
    """Keep the PostgreSQL connection alive for the compiled graph lifetime."""
    if settings.strict_checkpoint_msgpack:
        os.environ["LANGGRAPH_STRICT_MSGPACK"] = "true"

    # Lazy import keeps ordinary graph imports/tests independent of PostgreSQL.
    from agent_service.runtime.langgraph.checkpointer import create_checkpointer

    from integrations.executor.client import ExecutorClient
    async with ExecutorClient(settings) as executor_client, create_checkpointer(
        database_url=settings.checkpoint_db_uri,
        setup_on_start=settings.checkpoint_setup_on_start,
        min_size=settings.checkpoint_pool_min_size,
        max_size=settings.checkpoint_pool_max_size,
        timeout=settings.checkpoint_pool_timeout_seconds,
    ) as checkpointer:
        yield build_analysis_workflow_graph(
            deps,
            settings,
            checkpointer=checkpointer,
            executor_client=executor_client,
            submit_execution_start=submit_execution_start,
            submit_execution_continue=submit_execution_continue,
            submit_execution_finish=submit_execution_finish,
            bindings=bindings,
            execution_result_reader=execution_result_reader,
            workflow_store=workflow_store_from_environment(),
        )


def compiled_in_memory_graph(
    deps: AgentDependencies,
    settings: AgentSettings,
    *,
    executor_client: Any | None = None,
    submit_execution_start: Any | None = None,
    submit_execution_continue: Any | None = None,
    submit_execution_finish: Any | None = None,
    bindings: Any | None = None,
    execution_result_reader: Any | None = None,
):
    """Test-only graph; the service owns its separate asynchronous runtime."""
    from langgraph.checkpoint.memory import InMemorySaver

    return build_analysis_workflow_graph(
        deps,
        settings,
        executor_client=executor_client,
        checkpointer=InMemorySaver(),
        submit_execution_start=submit_execution_start,
        submit_execution_continue=submit_execution_continue,
        submit_execution_finish=submit_execution_finish,
        bindings=bindings,
        execution_result_reader=execution_result_reader,
    )


__all__ = ["compiled_in_memory_graph", "compiled_postgres_graph"]
