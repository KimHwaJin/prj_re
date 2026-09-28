"""Run/stream LangGraph while persisting emitted graph events through CRUD."""

from __future__ import annotations

from typing import Any, AsyncIterator
from uuid import UUID
from app.core.run_diagnostics import span

from app.services.graph_event_persistence import (
    GraphPersistenceContext,
    GraphPersistenceCursor,
    GraphPersistenceDispatcher,
)


async def _link_graph_task(db: Any, state: Any, agent_run_id: UUID | str | None) -> None:
    if not isinstance(state, dict) or agent_run_id is None:
        return
    graph_task_id = state.get("task_id")
    if not graph_task_id:
        return
    from app.services.task_service import TaskService

    await TaskService.attach_graph_task_for_run(
        db,
        run_id=UUID(str(agent_run_id)),
        graph_task_id=UUID(str(graph_task_id)),
    )


async def astream_with_crud_message_persistence(
    graph: Any,
    graph_input: Any,
    *,
    db: Any,
    user_id: UUID,
    config: dict[str, Any],
    stream_mode: str = "values",
    dispatcher: GraphPersistenceDispatcher | None = None,
    agent_run_id: UUID | str | None = None,
    trigger_message_id: UUID | str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield graph states while persisting newly emitted graph events.

    Keep the DB session outside graph state.  SQLAlchemy sessions are not
    checkpoint-serializable, while LangGraph state is expected to remain a
    JSON/msgpack-friendly domain state.
    """

    persistence = dispatcher or GraphPersistenceDispatcher.default()
    cursor = GraphPersistenceCursor()
    async for state in graph.astream(
        graph_input,
        config=config,
        stream_mode=stream_mode,
    ):
        if isinstance(state, dict):
            await _link_graph_task(db, state, agent_run_id or state.get("run_id"))
            context = GraphPersistenceContext.from_state(
                state,
                user_id=user_id,
                trigger_message_id=trigger_message_id,
                agent_run_id=agent_run_id or state.get("run_id"),
            )
            result = await persistence.persist_state_delta(
                db,
                state,
                cursor,
                context=context,
            )
            cursor = result.cursor
        yield state


async def ainvoke_with_crud_message_persistence(
    graph: Any,
    graph_input: Any,
    *,
    db: Any,
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
        with span("persistence.link_task"):
            await _link_graph_task(db, state, agent_run_id or state.get("run_id"))
        persistence = dispatcher or GraphPersistenceDispatcher.default()
        context = GraphPersistenceContext.from_state(
            state,
            user_id=user_id,
            trigger_message_id=trigger_message_id,
            agent_run_id=agent_run_id or state.get("run_id"),
        )
        with span("persistence.state_delta"):
            await persistence.persist_state_delta(
                db,
                state,
                GraphPersistenceCursor(),
                context=context,
            )
    return state


__all__ = [
    "ainvoke_with_crud_message_persistence",
    "astream_with_crud_message_persistence",
]
