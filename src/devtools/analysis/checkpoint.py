"""Developer-only synchronous graph helpers for CLI and offline tests.

The service uses agent_service.runtime.langgraph.checkpointer instead.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator

from agent_config import AgentSettings
from agent_service.agents.analysis.dependencies import AgentDependencies
from agent_service.agents.analysis.graph import (
    build_analysis_workflow_graph,
)
from app.services.workflow_persistence import workflow_store_from_environment


@contextmanager
def compiled_postgres_graph(
    deps: AgentDependencies,
    settings: AgentSettings,
    *,
    submit_execution_start: Any | None = None,
    submit_execution_continue: Any | None = None,
    submit_execution_finish: Any | None = None,
    bindings: Any | None = None,
    execution_result_reader: Any | None = None,
) -> Iterator[object]:
    """Keep the PostgreSQL connection alive for the compiled graph lifetime."""
    if settings.strict_checkpoint_msgpack:
        os.environ["LANGGRAPH_STRICT_MSGPACK"] = "true"

    # Lazy import keeps ordinary graph imports/tests independent of PostgreSQL.
    from langgraph.checkpoint.postgres import PostgresSaver

    with PostgresSaver.from_conn_string(settings.checkpoint_db_uri) as checkpointer:
        if settings.checkpoint_setup_on_start:
            checkpointer.setup()
        yield build_analysis_workflow_graph(
            deps,
            settings,
            checkpointer=checkpointer,
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
        checkpointer=InMemorySaver(),
        submit_execution_start=submit_execution_start,
        submit_execution_continue=submit_execution_continue,
        submit_execution_finish=submit_execution_finish,
        bindings=bindings,
        execution_result_reader=execution_result_reader,
    )


__all__ = ["compiled_in_memory_graph", "compiled_postgres_graph"]
