"""Standalone Worker entrypoint with no dependency on the source service."""

from __future__ import annotations

import asyncio
import logging
import signal
from typing import Any

from app.agent_worker.graph_provider import build_agent_graph
from app.agent_worker.langgraph_adapter import LangGraphEventAdapter
from app.agent_worker.worker_hooks import build_handlers
from app.worker import EventContext, ExecutorWorker
from app.worker.contracts import EventHandler
from app.services.session_execution import run_event_owned
from app.core.execution_lifecycle import execution_health


class DeferredHandler:
    """Break the Worker → bindings → graph → handler construction cycle."""

    def __init__(self) -> None:
        self._target: EventHandler | None = None

    def bind(self, target: EventHandler) -> None:
        if self._target is not None:
            raise RuntimeError("Handler is already bound")
        self._target = target

    async def __call__(self, context: EventContext) -> None:
        if self._target is None:
            raise RuntimeError("Agent graph handler is not initialized")
        await self._target(context)

    async def ready(self) -> bool:
        return self._target is not None


def _validate_graph(graph: Any) -> None:
    required = ("aget_state", "ainvoke")
    if not all(callable(getattr(graph, name, None)) for name in required):
        raise TypeError(
            "Graph must provide async get-state and invoke methods"
        )
    if getattr(graph, "checkpointer", None) in (None, False):
        raise ValueError("Graph must use a persistent checkpointer")


def _install_signal_handlers(worker: ExecutorWorker) -> list[signal.Signals]:
    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signum, worker.request_stop)
        except NotImplementedError:
            continue
        installed.append(signum)
    return installed


async def main(*, install_signals: bool = True, stop_event: asyncio.Event | None = None) -> None:
    from service_settings import get_settings
    from agent_service.runtime.langgraph.checkpointer import create_checkpointer
    service = get_settings()
    worker_settings = service.worker
    deferred = DeferredHandler()
    handlers = build_handlers(deferred)
    if not handlers or any(
        not event_type.strip() or not callable(handler)
        for event_type, handler in handlers.items()
    ):
        raise ValueError("build_handlers() must return valid handlers")

    async with ExecutorWorker(worker_settings, handlers) as worker:
        # Compile/open once per Worker lifespan, not once per Redis event.
        async with create_checkpointer(
            database_url=service.agent.checkpoint_db_uri,
            setup_on_start=service.agent.checkpoint_setup_on_start,
        ) as checkpointer:
            graph = build_agent_graph(bindings=worker.bindings, checkpointer=checkpointer)
            _validate_graph(graph)
            from app.services.agent_project_context import load_event_project_snapshot
            from app.services.executor_completion import synchronize_executor_completion
            adapter = LangGraphEventAdapter(graph, project_context_loader=load_event_project_snapshot)

            async def handle_event(context: EventContext) -> None:
                async def invoke_and_project():
                    await adapter(context)
                    # Receipt replay retries API projection after a separate graph commit.
                    await synchronize_executor_completion(context, graph)
                await run_event_owned(context, invoke_and_project)

            deferred.bind(handle_event)
            worker.add_readiness_check("agent-graph", deferred.ready)
            async def execution_ready():
                return execution_health.healthy
            worker.add_readiness_check("session-execution", execution_ready)
            installed = _install_signal_handlers(worker) if install_signals else []
            try:
                if stop_event is None:
                    await worker.run()
                else:
                    await worker.run(stop_event=stop_event)
            finally:
                loop = asyncio.get_running_loop()
                for signum in installed:
                    loop.remove_signal_handler(signum)


def run_worker() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())


if __name__ == "__main__":
    run_worker()
