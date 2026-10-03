"""Service context ownership and initial/resume boundary tests without external DB."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from api_service.services.agent_project_context import load_project_snapshot
from api_service.services import agent_graph_service as service
from api_service.services import graph_crud_persistence as projection
from api_service.runs.graph_invocation import GraphInvocation


def context_db(prompt, version, settings=None):
    project=SimpleNamespace(system_prompt=prompt,prompt_version=version)
    result=SimpleNamespace(one_or_none=Mock(return_value=(project,settings or {})))
    return SimpleNamespace(close=AsyncMock(),execute=AsyncMock(return_value=result))


@pytest.mark.asyncio
async def test_context_query_is_scoped_to_user_session_and_project():
    user_id, session_id, project_id = uuid4(), uuid4(), uuid4()
    db = context_db("project", 9)
    assert await load_project_snapshot(db, user_id=user_id, session_id=session_id, project_id=project_id) == {
        "project_system_prompt": "project", "project_prompt_version": 9}
    query = db.execute.call_args.args[0]
    assert set(query.compile().params.values()) == {user_id, session_id, project_id}
    assert "sessions.user_id" in str(query) and "sessions.session_id" in str(query)
    db.execute.return_value.one_or_none.return_value = None
    with pytest.raises(ValueError, match="does not belong"):
        await load_project_snapshot(db, user_id=user_id, session_id=session_id, project_id=project_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_initial_boundary_loads_snapshot_and_passes_it_to_graph(monkeypatch, stream):
    user_id, session_id, project_id, run_id = [uuid4() for _ in range(4)]
    db = context_db("server project prompt", 2)
    received = []
    from api_service.runs.protocols import initial
    saved = SimpleNamespace(values={}, tasks=(), next=())
    async def invoke(value, **kwargs):
        db.close.assert_awaited_once()
        received.append(value)
        saved.values = {**value, "initial_request_receipt": value["initial_request_identity"]}
        return saved.values
    graph = SimpleNamespace(aget_state=AsyncMock(return_value=saved), ainvoke=invoke)
    monkeypatch.setattr(initial, "mark_started", AsyncMock())
    monkeypatch.setattr(projection, "persist_graph_state", AsyncMock(return_value={}))
    async def astream(graph, value, **kwargs):
        db.close.assert_awaited_once()
        received.append(value)
        yield value
    monkeypatch.setattr(service, "astream_with_crud_message_persistence", astream)
    args = dict(user_id=user_id, project_id=project_id, session_id=session_id, run_id=run_id,
        user_request="hello", graph=graph)
    if stream:
        async for _ in service.astream_user_turn(session_factory=lambda: db, **args):
            pass
    else:
        await service.ainvoke_user_turn(session_factory=lambda: db, initial_protocol=1, **args)
    assert received[0]["project_system_prompt"] == "server project prompt"
    assert received[0]["project_prompt_version"] == 2
    assert received[0]["user_request"] == "hello"
    db.execute.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [None, "", "saved prompt"])
async def test_project_context_helper_only_backfills_legacy_snapshot(monkeypatch, existing):
    user_id, session_id, run_id, project_id = [uuid4() for _ in range(4)]
    values = {"user_id": str(user_id), "session_id": str(session_id), "project_id": str(project_id)}
    from service_runtime.model_selection import current_catalog
    values["model_selection"] = current_catalog().select().model_dump()
    if existing is not None:
        values["project_system_prompt"] = existing
    graph = SimpleNamespace(aget_state=AsyncMock(return_value=SimpleNamespace(values=values)), aupdate_state=AsyncMock())
    db = context_db("new prompt", 3)
    from api_service.services.agent_project_context import ensure_project_snapshot
    await ensure_project_snapshot(graph, {}, session_factory=lambda: db,
                                  user_id=user_id, session_id=session_id)
    if existing is None:
        db.execute.assert_awaited_once()
        assert graph.aupdate_state.call_args.args[1]["project_system_prompt"] == "new prompt"
    else:
        db.execute.assert_not_called()
        graph.aupdate_state.assert_not_called()


@pytest.mark.asyncio
async def test_executor_resume_backfills_once_then_reuses_snapshot():
    values = {"user_id": "user", "project_id": "project", "session_id": "session"}
    async def update(config, change):
        values.update(change)
    graph = SimpleNamespace(aget_state=AsyncMock(return_value=SimpleNamespace(values=values)),
        aupdate_state=AsyncMock(side_effect=update), ainvoke=AsyncMock(return_value={}))
    loader = AsyncMock(return_value={"project_system_prompt": "executor project", "project_prompt_version": 1})
    adapter = GraphInvocation(graph, project_context_loader=loader, model_validator=None)
    await adapter.invoke(None, {}, values=values, durability="sync")
    await adapter.invoke(None, {}, values=values, durability="sync")
    loader.assert_awaited_once()
    assert graph.ainvoke.await_count == 2
    assert values["project_system_prompt"] == "executor project"
