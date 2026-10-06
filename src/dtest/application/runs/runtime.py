"""API-boundary LangGraph runtime.

Graph nodes stay free of service DB/CRUD calls. The Worker passes plain
execution values; this boundary loads project context and projects results in
separate short sessions. Never pass an open caller session into graph execution.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

from dtest.application.runs.persistence.events import (
    GraphPersistenceDispatcher,
)
from dtest.application.runs.persistence.graph import (
    astream_with_crud_message_persistence,
)
from dtest.application.runs.project_context import read_project_snapshot
from dtest.contracts.enums import AgentRunStatus
from dtest.contracts.errors import ApplicationError
from dtest.infrastructure.observability.diagnostics import (
    graph_callbacks,
    register_pool_trace,
)
from dtest.lifecycle import protected_cleanup
from dtest.settings.agent import build_langgraph_thread_id

GRAPH_MESSAGE_SOURCE = "dtest-agent"
logger = logging.getLogger(__name__)


_composition = None
_assets_factory = None


def install_composition(composition, assets_factory):
    global _composition, _assets_factory
    _composition, _assets_factory = composition, assets_factory


def deployed_analysis_assets():
    if _assets_factory is None:
        raise RuntimeError(
            "Service container must be installed before accessing Agent assets"
        )
    return _assets_factory()


class GraphResourcesBusy(RuntimeError):
    """A borrower still owns graph resources; do not close dependent pools."""


class AgentGraphRuntime:
    """One compiled graph/resource stack per application lifecycle/event loop.

    Every invocation borrows through open_graph(). A borrow owns no database
    connection: the shared checkpointer borrows connections for individual I/O.
    """

    def __init__(self) -> None:
        self._graph: Any | None = None
        self._stack: AsyncExitStack | None = None
        self._init_lock = asyncio.Lock()
        self._active = 0
        self._drained = asyncio.Event()
        self._drained.set()
        self._closing = False
        self._loop = None
        self._observed_pools = ()

    def override_graph(self, graph: Any | None) -> None:
        if self._stack is not None or self._active or self._closing:
            raise RuntimeError(
                "Shutdown and restart runtime before overriding the graph"
            )
        self._graph = graph

    def _worker_settings(self) -> Any:
        from dtest.settings.loader import get_settings

        return get_settings().worker

    def _load_graph_inputs(self):
        if _composition is None:
            raise RuntimeError("Service container is not installed")
        return _composition.load_inputs()

    @asynccontextmanager
    async def _graph_context(self):
        if _composition is None:
            raise RuntimeError("Service container is not installed")
        async with _composition.open_graph(self) as graph:
            yield graph

    def start(self) -> None:
        """Accept borrows for a new lifespan, without opening any connections."""
        if self._active or self._stack is not None or self._init_lock.locked():
            raise GraphResourcesBusy(
                "Graph runtime still owns resources from another lifespan"
            )
        self._closing = False
        self._loop = None
        self._init_lock = asyncio.Lock()
        self._drained = asyncio.Event()
        self._drained.set()

    def _check_loop(self):
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise GraphResourcesBusy(
                "Graph runtime cannot be shared across event loops"
            )
        self._loop = loop

    async def _initialize(self):
        stack = AsyncExitStack()
        try:
            graph = await stack.enter_async_context(self._graph_context())
        except BaseException:
            await stack.aclose()
            raise
        self._stack, self._graph = stack, graph

    @asynccontextmanager
    async def open_graph(self) -> AsyncIterator[Any]:
        self._check_loop()
        if self._closing:
            raise GraphResourcesBusy("Graph runtime is shutting down")
        async with self._init_lock:
            if self._closing:
                raise GraphResourcesBusy("Graph runtime is shutting down")
            if self._graph is None:
                # Caller cancellation cannot interrupt shared resource creation
                # midway or make another caller initialize a second pool.
                await protected_cleanup(self._initialize())
            if self._closing:
                raise GraphResourcesBusy("Graph runtime is shutting down")
            graph = self._graph
            for pool, name in self._observed_pools:
                register_pool_trace(pool, name)
            self._active += 1
            self._drained.clear()
        try:
            yield graph
        finally:
            # No await: repeated borrower cancellation cannot lose its release.
            self._active -= 1
            if not self._active:
                self._drained.set()

    async def shutdown(self, *, timeout: float | None = None) -> None:
        from dtest.settings.loader import get_settings

        # A never-opened runtime is safe to stop from an API-only lifespan.
        if self._loop is not None:
            self._check_loop()
        self._closing = True
        timeout = (
            get_settings().shutdown_timeout_seconds
            if timeout is None
            else timeout
        )
        try:
            async with asyncio.timeout(timeout):
                async with self._init_lock:
                    await self._drained.wait()
                    stack = self._stack
                    if stack is not None:
                        await protected_cleanup(stack.aclose())
                    self._stack = self._graph = None
                    self._observed_pools = ()
                    self._loop = None
        except TimeoutError as exc:
            # Keep references and reject new borrows. A later shutdown can retry
            # after existing borrowers release; never close underneath them.
            raise GraphResourcesBusy(
                "Graph shutdown deadline exceeded"
            ) from exc


runtime = AgentGraphRuntime()


def user_request_from_messages(messages: list[dict[str, Any]]) -> str:
    for item in reversed(messages):
        if not isinstance(item, dict):
            continue
        if item.get("role") not in {"user", "human"}:
            continue
        content = item.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            parts = [
                part.get("text", "")
                for part in content
                if isinstance(part, dict)
            ]
            text = "".join(parts).strip()
            if text:
                return text
    raise ApplicationError(
        status_code=422,
        detail="Run input must include a user message with text content.",
    )


def interrupt_payload(
    state: Mapping[str, Any] | None,
) -> list[dict[str, Any]] | None:
    if not state:
        return None
    interrupts = state.get("__interrupt__") or []
    if not interrupts:
        return None
    payload: list[dict[str, Any]] = []
    for item in interrupts:
        value = getattr(item, "value", item)
        if isinstance(value, dict):
            payload.append(value)
        else:
            payload.append({"value": value})
    return payload or None


def run_status_from_state(state: Mapping[str, Any] | None) -> AgentRunStatus:
    if interrupt_payload(state):
        return AgentRunStatus.INTERRUPTED
    if (state or {}).get("agent_runtime") == "agentic-planning-v1" and (
        state.get("final_response") or {}
    ).get("status") == "analysis_failed":
        return AgentRunStatus.ERROR
    return AgentRunStatus.SUCCESS


def build_graph_input(
    *,
    user_id: UUID,
    project_id: UUID,
    session_id: UUID,
    run_id: UUID | str,
    user_request: str,
    trigger_message_id: UUID | str | None = None,
    request_id: str | None = None,
    model_selection: dict[str, str] | None = None,
) -> dict[str, Any]:
    from dtest.contracts.model_selection import current_catalog

    if model_selection is None:
        model_selection = current_catalog().select().model_dump()
    current_catalog().resolve(model_selection)
    run_key = str(run_id)
    thread_id = build_langgraph_thread_id(str(session_id))
    graph_input = {
        "model_selection": model_selection,
        "user_id": str(user_id),
        "project_id": str(project_id),
        "session_id": str(session_id),
        "run_id": run_key,
        "thread_id": thread_id,
        "request_id": request_id or str(uuid4()),
        "user_request": user_request,
        "agent_run_id": run_key,
    }
    if trigger_message_id is not None:
        graph_input["trigger_message_id"] = str(trigger_message_id)
    return graph_input


def graph_config(
    session_id: UUID | str,
    run_id: UUID | str,
    callbacks: list[Any] | None = None,
) -> dict[str, Any]:
    thread_id = build_langgraph_thread_id(str(session_id))
    from dtest.settings.loader import get_settings

    config: dict[str, Any] = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": get_settings().agent.recursion_limit,
    }
    observed_callbacks = graph_callbacks(
        _composition.callbacks(callbacks or [])
        if _composition is not None
        else callbacks
    )
    if observed_callbacks:
        config["callbacks"] = observed_callbacks
    return config


async def ainvoke_user_turn(
    *,
    session_factory: Any | None = None,
    user_id: UUID,
    project_id: UUID,
    session_id: UUID,
    run_id: UUID,
    user_request: str,
    initial_protocol: int | None = None,
    initial_started: bool = False,
    trigger_message_id: UUID | str | None = None,
    request_id: str | None = None,
    model_selection: dict[str, str] | None = None,
    dispatcher: GraphPersistenceDispatcher | None = None,
    graph: Any | None = None,
    callbacks: list[Any] | None = None,
) -> dict[str, Any]:
    graph_input = build_graph_input(
        user_id=user_id,
        project_id=project_id,
        session_id=session_id,
        run_id=run_id,
        user_request=user_request,
        trigger_message_id=trigger_message_id,
        request_id=request_id,
        model_selection=model_selection,
    )
    from dtest.application.runs.graph_invocation import GraphInvocation

    async def invoke(compiled):
        return await GraphInvocation(
            compiled, session_factory=session_factory, dispatcher=dispatcher
        ).user_turn(
            graph_config(session_id, run_id, callbacks),
            graph_input,
            user_id=user_id,
            project_id=project_id,
            session_id=session_id,
            run_id=run_id,
            protocol=initial_protocol,
            started=initial_started,
            trigger_message_id=trigger_message_id,
        )

    if graph is not None:
        return await invoke(graph)
    async with runtime.open_graph() as compiled:
        return await invoke(compiled)


async def ainvoke_resume(
    *,
    session_factory: Any | None = None,
    user_id: UUID,
    session_id: UUID,
    checkpoint_run_id: UUID,
    agent_run_id: UUID | None = None,
    command: dict[str, Any] | str,
    resume_target: str | None = None,
    resume_started: bool = False,
    model_selection: dict[str, str] | None = None,
    dispatcher: GraphPersistenceDispatcher | None = None,
    graph: Any | None = None,
    callbacks: list[Any] | None = None,
) -> dict[str, Any]:
    from dtest.application.runs.graph_invocation import GraphInvocation

    async def invoke(compiled):
        return await GraphInvocation(
            compiled, session_factory=session_factory, dispatcher=dispatcher
        ).user_resume(
            graph_config(session_id, checkpoint_run_id, callbacks),
            user_id=user_id,
            run_id=agent_run_id,
            command=command,
            target=resume_target,
            started=resume_started,
            model_selection=model_selection,
        )

    if graph is not None:
        return await invoke(graph)
    async with runtime.open_graph() as compiled:
        return await invoke(compiled)


async def astream_user_turn(
    *,
    session_factory: Any | None = None,
    user_id: UUID,
    project_id: UUID,
    session_id: UUID,
    run_id: UUID,
    user_request: str,
    trigger_message_id: UUID | str | None = None,
    request_id: str | None = None,
    model_selection: dict[str, str] | None = None,
    dispatcher: GraphPersistenceDispatcher | None = None,
    graph: Any | None = None,
):
    graph_input = build_graph_input(
        user_id=user_id,
        project_id=project_id,
        session_id=session_id,
        run_id=run_id,
        user_request=user_request,
        trigger_message_id=trigger_message_id,
        request_id=request_id,
        model_selection=model_selection,
    )
    graph_input.update(
        await read_project_snapshot(
            user_id=user_id,
            session_id=session_id,
            project_id=project_id,
            session_factory=session_factory,
        )
    )
    if graph is None:
        async with runtime.open_graph() as compiled:
            async for state in astream_with_crud_message_persistence(
                compiled,
                graph_input,
                session_factory=session_factory,
                user_id=user_id,
                config=graph_config(session_id, run_id),
                dispatcher=dispatcher,
                agent_run_id=run_id,
                trigger_message_id=trigger_message_id,
            ):
                yield state
            return
    async for state in astream_with_crud_message_persistence(
        graph,
        graph_input,
        session_factory=session_factory,
        user_id=user_id,
        config=graph_config(session_id, run_id),
        dispatcher=dispatcher,
        agent_run_id=run_id,
        trigger_message_id=trigger_message_id,
    ):
        yield state


__all__ = [
    "GRAPH_MESSAGE_SOURCE",
    "AgentGraphRuntime",
    "ainvoke_resume",
    "ainvoke_user_turn",
    "astream_user_turn",
    "build_graph_input",
    "graph_config",
    "interrupt_payload",
    "run_status_from_state",
    "runtime",
    "user_request_from_messages",
]
