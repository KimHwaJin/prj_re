"""Service context ownership and initial/resume boundary tests without external DB."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.agent_project_context import load_project_snapshot
from app.services import agent_graph_service as service
from app.agent_worker.langgraph_adapter import LangGraphEventAdapter


@pytest.mark.asyncio
async def test_context_query_is_scoped_to_user_session_and_project():
    user_id, session_id, project_id = uuid4(), uuid4(), uuid4()
    db = SimpleNamespace(close=AsyncMock(), scalar=AsyncMock(return_value=SimpleNamespace(system_prompt="project", prompt_version=9)))
    assert await load_project_snapshot(db, user_id=user_id, session_id=session_id, project_id=project_id) == {
        "project_system_prompt": "project", "project_prompt_version": 9}
    query = db.scalar.call_args.args[0]
    assert set(query.compile().params.values()) == {user_id, session_id, project_id}
    assert "sessions.user_id" in str(query) and "sessions.session_id" in str(query)
    db.scalar.return_value = None
    with pytest.raises(ValueError, match="does not belong"):
        await load_project_snapshot(db, user_id=user_id, session_id=session_id, project_id=project_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_initial_boundary_loads_snapshot_and_passes_it_to_graph(monkeypatch, stream):
    user_id, session_id, project_id, run_id = [uuid4() for _ in range(4)]
    db = SimpleNamespace(close=AsyncMock(), scalar=AsyncMock(return_value=SimpleNamespace(system_prompt="server project prompt", prompt_version=2)))
    received = []
    async def invoke(graph, value, **kwargs):
        db.close.assert_awaited_once()
        received.append(value)
        return value
    async def astream(graph, value, **kwargs):
        db.close.assert_awaited_once()
        received.append(value)
        yield value
    monkeypatch.setattr(service, "ainvoke_with_crud_message_persistence", invoke)
    monkeypatch.setattr(service, "astream_with_crud_message_persistence", astream)
    args = dict(user_id=user_id, project_id=project_id, session_id=session_id, run_id=run_id,
        user_request="hello", graph=object())
    if stream:
        async for _ in service.astream_user_turn(session_factory=lambda: db, **args):
            pass
    else:
        await service.ainvoke_user_turn(session_factory=lambda: db, **args)
    assert received[0]["project_system_prompt"] == "server project prompt"
    assert received[0]["project_prompt_version"] == 2
    assert received[0]["user_request"] == "hello"
    db.scalar.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [None, "", "saved prompt"])
async def test_resume_only_backfills_legacy_snapshot(monkeypatch, existing):
    user_id, session_id, run_id, project_id = [uuid4() for _ in range(4)]
    values = {"user_id": str(user_id), "session_id": str(session_id), "project_id": str(project_id)}
    if existing is not None:
        values["project_system_prompt"] = existing
    graph = SimpleNamespace(aget_state=AsyncMock(return_value=SimpleNamespace(values=values)), aupdate_state=AsyncMock())
    db = SimpleNamespace(close=AsyncMock(), scalar=AsyncMock(return_value=SimpleNamespace(system_prompt="new prompt", prompt_version=3)))
    persist = AsyncMock(return_value={})
    monkeypatch.setattr(service, "ainvoke_with_crud_message_persistence", persist)
    await service.ainvoke_resume(session_factory=lambda: db, user_id=user_id, session_id=session_id, checkpoint_run_id=run_id,
        command={"approved": True}, graph=graph)
    persist.assert_awaited_once()
    if existing is None:
        db.scalar.assert_awaited_once()
        assert graph.aupdate_state.call_args.args[1]["project_system_prompt"] == "new prompt"
    else:
        db.scalar.assert_not_called()
        graph.aupdate_state.assert_not_called()


@pytest.mark.asyncio
async def test_executor_resume_backfills_once_then_reuses_snapshot():
    values = {"user_id": "user", "project_id": "project", "session_id": "session"}
    async def update(config, change):
        values.update(change)
    graph = SimpleNamespace(aget_state=AsyncMock(return_value=SimpleNamespace(values=values)),
        aupdate_state=AsyncMock(side_effect=update), ainvoke=AsyncMock(return_value={}))
    loader = AsyncMock(return_value={"project_system_prompt": "executor project", "project_prompt_version": 1})
    adapter = LangGraphEventAdapter(graph, project_context_loader=loader)
    await adapter._invoke(None, {}, values=values, durability="sync")
    await adapter._invoke(None, {}, values=values, durability="sync")
    loader.assert_awaited_once()
    assert graph.ainvoke.await_count == 2
    assert values["project_system_prompt"] == "executor project"
