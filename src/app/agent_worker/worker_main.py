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


async def main(*, install_signals: bool = True) -> None:
    from service_settings import get_settings
    from app.graphs.checkpointer_factory import create_checkpointer
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
        async def resume_graph(context: EventContext) -> None:
            async with create_checkpointer(
                database_url=service.agent.checkpoint_db_uri,
                setup_on_start=service.agent.checkpoint_setup_on_start,
            ) as checkpointer:
                graph = build_agent_graph(bindings=worker.bindings, checkpointer=checkpointer)
                _validate_graph(graph)
                await LangGraphEventAdapter(graph)(context)
        deferred.bind(resume_graph)
        worker.add_readiness_check("agent-graph", deferred.ready)
        installed = _install_signal_handlers(worker) if install_signals else []
        try:
            await worker.run()
        finally:
            loop = asyncio.get_running_loop()
            for signum in installed:
                loop.remove_signal_handler(signum)


def run_worker() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())


if __name__ == "__main__":
    run_worker()
