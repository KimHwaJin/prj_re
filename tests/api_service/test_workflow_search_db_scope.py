"""Search identity lifetime with HTTP dependencies and DB/search doubles."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from dtest.api_service.auth.dependencies import get_login_session
from dtest.api_service.http.dependencies import CurrentUserId
from dtest.api_service.http.v1.routes.workflows import router
from dtest.application.workflows import queries
from dtest.contracts.enums import UserRole
from dtest.infrastructure.database import runtime as database
from dtest.infrastructure.redis.login_sessions import LoginSession


@pytest_asyncio.fixture
async def boundary(monkeypatch):
    user_id = uuid4()
    db = AsyncMock(spec=AsyncSession)
    db.__aenter__.return_value = db
    db.scalar.return_value = SimpleNamespace(
        user_id=user_id, public_user_id="review", role=UserRole.USER
    )
    monkeypatch.setattr(database, "get_session_factory", lambda: lambda: db)
    app = FastAPI()
    app.include_router(router)
    state = SimpleNamespace(
        login=LoginSession(str(user_id), "a" * 43, 9999999999),
        db=db,
        user_id=user_id,
        searched=False,
    )

    async def identity():
        if state.login is None:
            raise HTTPException(401, "Login required")
        return state.login

    app.dependency_overrides[get_login_session] = identity

    async def search(query):
        state.searched = True
        assert db.close.await_count == 1
        return {"items": [], "diagnostics": {"termination": "disabled"}}

    monkeypatch.setattr(queries, "search_workflows", search)

    @app.post("/change")
    async def change(user_id: CurrentUserId):
        assert db.close.await_count == 0
        statement = db.scalar.call_args.args[0]
        assert statement._for_update_arg.read
        return {"user_id": str(user_id)}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        state.client = client
        yield state


@pytest.mark.asyncio
async def test_search_closes_identity_session_before_backend(boundary):
    h = boundary
    response = await h.client.post(
        "/workflows/search", json={"query": "analysis"}
    )
    assert response.status_code == 200, response.text
    assert h.searched
    h.db.close.assert_awaited_once()
    assert h.db.scalar.call_args.args[0]._for_update_arg is None


@pytest.mark.asyncio
async def test_inactive_user_cannot_search_and_session_is_closed(boundary):
    h = boundary
    h.db.scalar.return_value = None
    response = await h.client.post(
        "/workflows/search", json={"query": "analysis"}
    )
    assert response.status_code == 401
    assert not h.searched
    h.db.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalid_identity_cannot_search_and_session_is_closed(boundary):
    h = boundary
    h.login = LoginSession("invalid", "a" * 43, 9999999999)
    response = await h.client.post(
        "/workflows/search", json={"query": "analysis"}
    )
    assert response.status_code == 401
    assert not h.searched
    h.db.scalar.assert_not_awaited()
    h.db.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_login_never_opens_identity_query(boundary):
    h = boundary
    h.login = None
    response = await h.client.post(
        "/workflows/search", json={"query": "analysis"}
    )
    assert response.status_code == 401
    assert not h.searched
    h.db.scalar.assert_not_awaited()
    h.db.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_change_keeps_shared_lock_through_handler(boundary):
    h = boundary
    response = await h.client.post("/change")
    assert response.status_code == 200, response.text
    assert response.json()["user_id"] == str(h.user_id)
    assert h.db.__aexit__.await_count == 1


@pytest.mark.asyncio
async def test_search_failure_does_not_retain_identity_session(
    boundary, monkeypatch
):
    h = boundary

    async def failing_search(query):
        h.db.close.assert_awaited_once()
        raise TimeoutError("Search failed")

    monkeypatch.setattr(queries, "search_workflows", failing_search)
    with pytest.raises(TimeoutError):
        await h.client.post("/workflows/search", json={"query": "analysis"})
    h.db.close.assert_awaited_once()
