from __future__ import annotations

"""Explicit composition of Agent, checkpoint, Store and Executor resources."""
from contextlib import AsyncExitStack, asynccontextmanager
from functools import lru_cache
import asyncio
from typing import Any, AsyncIterator
from dtest.settings.agent import load_agent_settings
from dtest.infrastructure.observability.diagnostics import span


@lru_cache(maxsize=1)
def deployed_analysis_assets():
    """Shared immutable assets for Workflow validation and graph composition.

    Reading metadata opens no model client, graph, database or Executor resource.
    Source/metadata changes take effect on deployment, never during approval.
    """
    from dtest.agent_service.agents.analysis.planning.catalog import (
        AssetCatalog,
    )

    return AssetCatalog()


class ServiceContainer:
    def callbacks(self, callbacks):
        from dtest.application.runs.token_events import LLMTokenEventBuffer
        from dtest.agent_service.runtime.callbacks import TokenCallbacks

        return [
            TokenCallbacks(item)
            if isinstance(item, LLMTokenEventBuffer)
            else item
            for item in callbacks
        ]

    def load_inputs(self) -> tuple[Any, Any, str]:
        try:
            from dtest.agent_service.agents.analysis.planning.runtime import (
                PlanningRuntime,
            )
        except ImportError as exc:
            raise RuntimeError(
                f"Agent graph dependencies are not available: {exc}"
            ) from exc

        agent_settings = load_agent_settings()
        from dtest.application.resources.project_memory import (
            ProjectMemoryPolicy,
        )

        dependencies = PlanningRuntime(
            agent_settings,
            catalog=deployed_analysis_assets(),
            memory_policy_factory=ProjectMemoryPolicy().for_context,
        )
        checkpointer = agent_settings.graph_checkpointer.strip().lower()
        return dependencies, agent_settings, checkpointer

    @asynccontextmanager
    async def open_graph(self, owner) -> AsyncIterator[Any]:
        with span("runtime.dependencies"):
            planning, agent_settings, kind = await asyncio.to_thread(
                owner._load_graph_inputs
            )
        from dtest.agent_service.agents.analysis.planning.graph import (
            build_planning_graph,
        )
        from dtest.agent_service.runtime.langgraph.checkpointer import (
            create_checkpointer,
        )
        from langgraph.checkpoint.memory import InMemorySaver
        from dtest.infrastructure.memory.store import runtime as store_runtime

        async with AsyncExitStack() as stack:
            from dtest.infrastructure.workflow_search.runtime import (
                get_workflow_runtime,
            )

            planning.workflow_retriever = get_workflow_runtime().search
            from dtest.settings.loader import get_settings

            planning.workflow_context_max_chars = (
                get_settings().workflow_search.context_max_chars
            )
            if agent_settings.agent_project_memory_mode != "off":
                planning.store = await stack.enter_async_context(
                    store_runtime.open_store()
                )
            if kind == "postgres":
                saver = await stack.enter_async_context(
                    create_checkpointer(
                        database_url=agent_settings.checkpoint_db_uri,
                        setup_on_start=agent_settings.checkpoint_setup_on_start,
                    )
                )
                pools = [(saver.conn, "checkpoint_pool")]
                if agent_settings.executor_submit_enabled:
                    from dtest.infrastructure.executor.client import (
                        ExecutorClient,
                    )
                    from dtest.infrastructure.database.executor_bindings import (
                        ApiWorkerBridge,
                    )

                    planning.executor = await stack.enter_async_context(
                        ExecutorClient(agent_settings)
                    )
                    bridge = await stack.enter_async_context(
                        ApiWorkerBridge(owner._worker_settings())
                    )
                    planning.bindings = bridge.bindings
                    pools.append((bridge.pool, "bridge_pool"))
            elif kind == "memory":
                saver, pools = InMemorySaver(), []
            else:
                raise RuntimeError(
                    "GRAPH_CHECKPOINTER must be postgres or memory"
                )
            store_pool = getattr(planning.store, "conn", None)
            if store_pool is not None:
                pools.append((store_pool, "memory_store_pool"))
            owner._observed_pools = tuple(pools)
            yield build_planning_graph(planning, checkpointer=saver)


container = ServiceContainer()


def install_container():
    from dtest.application.runs import runtime

    runtime.install_composition(container, deployed_analysis_assets)
    from dtest.contracts.agent import install_resume_factory
    from langgraph.types import Command

    install_resume_factory(Command)
    return container
