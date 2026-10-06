"""In-stream reuse never replaces committed event identity or recovery replay."""

from copy import deepcopy
from uuid import uuid4
from unittest.mock import AsyncMock
import pytest
import dtest.application.runs.persistence.graph as persistence


def state():
    value = {
        k: str(uuid4())
        for k in (
            "session_id",
            "project_id",
            "task_id",
            "agent_run_id",
            "user_id",
        )
    }
    value.update(
        agent_runtime="agentic-planning-v1",
        public_events=[
            {"event_id": str(uuid4()), "payload": {"text": "original"}}
        ],
    )
    return value


async def project(cursor, value):
    return await cursor.persist(
        value, user_id=value["user_id"], agent_run_id=value["agent_run_id"]
    )


@pytest.mark.asyncio
async def test_skip_identical_states_but_replay_new_invocation_and_changed_payload(
    monkeypatch,
):
    original = state()
    persist = AsyncMock()
    monkeypatch.setattr(persistence, "persist_graph_state", persist)
    cursor = persistence.InvocationProjection()
    await project(cursor, original)
    await project(cursor, deepcopy(original))
    assert persist.await_count == 1
    altered = deepcopy(original)
    altered["public_events"][0]["payload"]["text"] = "changed"
    await project(cursor, altered)
    assert persist.await_count == 2
    await project(persistence.InvocationProjection(), altered)
    assert persist.await_count == 3
    other = deepcopy(altered)
    other["agent_run_id"] = str(uuid4())
    await project(cursor, other)
    assert persist.await_count == 4
    assert original["public_events"][0]["payload"]["text"] == "original"


@pytest.mark.asyncio
async def test_failed_delta_is_not_marked_saved_and_original_state_remains_complete(
    monkeypatch,
):
    value = state()
    persist = AsyncMock()
    monkeypatch.setattr(persistence, "persist_graph_state", persist)
    cursor = persistence.InvocationProjection()
    await project(cursor, value)
    second = {"event_id": str(uuid4()), "payload": {"text": "second"}}
    value["public_events"].append(second)
    persist.side_effect = RuntimeError("Rollback")
    with pytest.raises(RuntimeError):
        await project(cursor, value)
    persist.side_effect = None
    await project(cursor, value)
    assert persist.call_args.args[0]["public_events"] == [second]
    assert len(value["public_events"]) == 2
    await project(cursor, value)
    assert persist.await_count == 3


@pytest.mark.asyncio
async def test_legacy_projection_preserves_full_state(monkeypatch):
    persist = AsyncMock()
    monkeypatch.setattr(persistence, "persist_graph_state", persist)
    value = state()
    value.pop("agent_runtime")
    cursor = persistence.InvocationProjection()
    await project(cursor, value)
    await project(cursor, value)
    assert persist.await_count == 2 and persist.call_args.args[0] is value


@pytest.mark.asyncio
async def test_custom_dispatcher_preserves_every_state_and_supplied_factory(
    monkeypatch,
):
    persist = AsyncMock()
    monkeypatch.setattr(persistence, "persist_graph_state", persist)
    value = state()
    cursor = persistence.InvocationProjection()
    custom = object()
    factory = object()
    for _ in range(2):
        await cursor.persist(
            value,
            user_id=value["user_id"],
            agent_run_id=value["agent_run_id"],
            dispatcher=custom,
            session_factory=factory,
        )
    assert persist.await_count == 2
    assert (
        persist.call_args.kwargs["dispatcher"] is custom
        and persist.call_args.kwargs["session_factory"] is factory
    )
