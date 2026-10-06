"""Run/stream LangGraph while persisting emitted graph events through CRUD."""

from __future__ import annotations

from typing import Any, AsyncIterator
from uuid import UUID
from dtest.infrastructure.observability.diagnostics import span
from dtest.infrastructure.database.runtime import short_session

from dtest.application.runs.persistence.events import (
    GraphPersistenceContext,
    GraphPersistenceCursor,
    GraphPersistenceDispatcher,
)


async def _link_graph_task(
    db: Any, state: Any, agent_run_id: UUID | str | None
) -> None:
    if not isinstance(state, dict) or agent_run_id is None:
        return
    graph_task_id = state.get("task_id")
    if not graph_task_id:
        return
    from dtest.application.runs.tasks import TaskService

    await TaskService.attach_graph_task_for_run(
        db,
        run_id=UUID(str(agent_run_id)),
        graph_task_id=UUID(str(graph_task_id)),
    )


async def astream_with_crud_message_persistence(
    graph: Any,
    graph_input: Any,
    *,
    session_factory: Any | None = None,
    user_id: UUID,
    config: dict[str, Any],
    stream_mode: str = "values",
    dispatcher: GraphPersistenceDispatcher | None = None,
    agent_run_id: UUID | str | None = None,
    trigger_message_id: UUID | str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield graph states while persisting newly emitted graph events.

    Open a short DB session for each projection and close it before yielding
    or requesting the next graph state. Never hold it across model waits or
    consumer backpressure.
    """

    persistence = dispatcher or GraphPersistenceDispatcher.default()
    cursor = GraphPersistenceCursor()
    async for state in graph.astream(
        graph_input,
        config=config,
        stream_mode=stream_mode,
    ):
        if isinstance(state, dict):
            cursor = await _persist_state(
                state,
                user_id=user_id,
                cursor=cursor,
                persistence=persistence,
                session_factory=session_factory,
                agent_run_id=agent_run_id,
                trigger_message_id=trigger_message_id,
            )
        yield state


async def ainvoke_with_crud_message_persistence(
    graph: Any,
    graph_input: Any,
    *,
    session_factory: Any | None = None,
    user_id: UUID,
    config: dict[str, Any],
    dispatcher: GraphPersistenceDispatcher | None = None,
    agent_run_id: UUID | str | None = None,
    trigger_message_id: UUID | str | None = None,
) -> dict[str, Any]:
    """Invoke graph once and persist events from the returned state."""

    with span("graph.invoke"):
        state = await graph.ainvoke(graph_input, config=config)
    if isinstance(state, dict):
        await _persist_state(
            state,
            user_id=user_id,
            cursor=GraphPersistenceCursor(),
            persistence=dispatcher or GraphPersistenceDispatcher.default(),
            session_factory=session_factory,
            agent_run_id=agent_run_id,
            trigger_message_id=trigger_message_id,
        )
    return state


async def persist_graph_state(
    state,
    *,
    user_id,
    session_factory=None,
    dispatcher=None,
    agent_run_id=None,
    trigger_message_id=None,
):
    """Repair service projections from an already durable graph snapshot."""
    await _persist_state(
        state,
        user_id=user_id,
        cursor=GraphPersistenceCursor(),
        persistence=dispatcher or GraphPersistenceDispatcher.default(),
        session_factory=session_factory,
        agent_run_id=agent_run_id,
        trigger_message_id=trigger_message_id,
    )
    return state


async def _persist_state(
    state,
    *,
    user_id,
    cursor,
    persistence,
    session_factory,
    agent_run_id,
    trigger_message_id,
):
    # Graph execution and each yielded state own no service DB connection.
    # The default dispatcher commits one result's messages/logs/events together.
    # Task identity linking and LangGraph checkpoints remain separate; custom
    # dispatchers retain their own commit contract.
    context = GraphPersistenceContext.from_state(
        state,
        user_id=user_id,
        trigger_message_id=trigger_message_id,
        agent_run_id=agent_run_id or state.get("run_id"),
    )
    async with short_session(session_factory) as db:
        with span("persistence.link_task"):
            await _link_graph_task(
                db, state, agent_run_id or state.get("run_id")
            )
        with span("persistence.state_delta"):
            result = await persistence.persist_state_delta(
                db, state, cursor, context=context
            )
        # Flush any final relationship changes that a handler leaves pending.
        await db.commit()
        return result.cursor


__all__ = [
    "ainvoke_with_crud_message_persistence",
    "astream_with_crud_message_persistence",
]


class InvocationProjection:
    """Skip only projections already committed during ONE values stream.

    Checkpoints/receipts still run at every super-step. The first projection and
    every new invocation replay all current public events through durable SQL
    deduplication; recovery continues to call persist_graph_state directly.
    Changed event payloads are sent to the same DB logic, never hidden by IDs.
    No DB connection, transaction or cursor is kept between graph states.
    """

    def __init__(self):
        self._scope = None
        self._events = {}

    async def persist(
        self,
        state,
        *,
        user_id,
        agent_run_id,
        session_factory=None,
        dispatcher=None,
        trigger_message_id=None,
    ):
        kwargs = dict(
            user_id=user_id,
            agent_run_id=agent_run_id,
            session_factory=session_factory,
            dispatcher=dispatcher,
            trigger_message_id=trigger_message_id,
        )
        if (
            dispatcher is not None
            or not isinstance(state, dict)
            or state.get("agent_runtime") != "agentic-planning-v1"
        ):
            return await persist_graph_state(state, **kwargs)
        import hashlib
        import json

        scope = (
            str(user_id),
            str(agent_run_id),
            str(trigger_message_id),
            *(
                str(state.get(key))
                for key in ("session_id", "project_id", "task_id")
            ),
        )
        prior = self._events if scope == self._scope else {}
        pending, fingerprints = [], {}
        for event in state.get("public_events", []):
            key = event["event_id"]
            digest = hashlib.sha256(
                json.dumps(
                    event,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                ).encode()
            ).digest()
            if prior.get(key) != digest:
                pending.append(event)
            fingerprints[key] = digest
        if scope != self._scope or pending:
            await persist_graph_state(
                {**state, "public_events": pending}, **kwargs
            )
            # Advance only after commit. A failed/uncertain projection is repaired
            # on retry, with exactly the same persistent unique keys and payloads.
            self._scope = scope
            self._events = {**prior, **fingerprints}
        return state
