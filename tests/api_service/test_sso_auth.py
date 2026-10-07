"""Production cookie/CSRF boundary with explicit SDK, Redis and user-DB test doubles."""

import asyncio
import json
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI, HTTPException
from redis.exceptions import ConnectionError as RedisConnectionError

import dtest.settings.loader as service_settings
from dtest.api_service.http.dependencies import (
    get_current_actor,
    get_current_user_id,
    get_stream_user_id,
)
from dtest.infrastructure.database.runtime import get_db
from dtest.contracts.enums import DeleteYN, UserRole
from dtest.infrastructure.database.repositories.user_repository import (
    UserRepository,
)
from dtest.contracts.resources.user_schema import UserRead
from dtest.application.resources.users import UserService
from dtest.infrastructure.sso.adapter import SyncSsoAdapter, load_adapter
from dtest.contracts.auth import VerifiedEmployee
from dtest.api_service.auth.runtime import attach_sso
from dtest.settings.auth import SsoSettings
from dtest.infrastructure.redis.login_sessions import RedisSessions
from dtest.bootstrap import create_app


class MemoryRedis:
    def __init__(self):
        self.data = {}
        self.now = 0
        self.fail = False
        self.reads = 0

    async def set(self, key, value, *, ex, nx):
        if self.fail:
            raise RedisConnectionError("private-url-with-secret")
        if nx and key in self.data and self.data[key][1] > self.now:
            return False
        self.data[key] = (value, self.now + ex)
        return True

    async def get(self, key):
        self.reads += 1
        if self.fail:
            raise RedisConnectionError("private-url-with-secret")
        value = self.data.get(key)
        return value[0].encode() if value and value[1] > self.now else None

    async def delete(self, key):
        if self.fail:
            raise RedisConnectionError("private-url-with-secret")
        return bool(self.data.pop(key, None))


class CorporateDouble:
    def __init__(self):
        self.employee = VerifiedEmployee("000123", "홍길동")
        self.url = "https://sso.example.test/login"
        self.callback = None
        self.error = None

    async def verify(self, request):
        if self.error:
            raise self.error
        return self.employee

    async def login_url(self, request, return_url):
        self.callback = return_url
        return self.url


class UserDouble:
    def __init__(self):
        self.id = uuid4()
        self.role = UserRole.USER
        self.active = True
        self.lock_flags = []

    async def bind(self, employee):
        return str(self.id)

    async def get_active(self, db, user_id, *, for_share=False):
        self.lock_flags.append(for_share)
        if self.active and user_id == self.id:
            return SimpleNamespace(
                user_id=self.id, public_user_id="000123", role=self.role
            )

    async def read(self, db, actor, public_id):
        now = datetime.now(timezone.utc)
        return UserRead(
            public_user_id="000123",
            user_name="홍길동",
            role=self.role,
            default_project_id=uuid4(),
            delete_yn=DeleteYN.N,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )


@pytest_asyncio.fixture
async def sso(monkeypatch):
    monkeypatch.setattr(service_settings, "_snapshot", None)
    settings = service_settings.load_settings(
        config={
            "MODEL_PROVIDER": "mock",
            "AGENT_WORKER_ENABLED": False,
            "TASK_RECONCILER_ENABLED": False,
            "EVENT_WORKER_ENABLED": False,
            "SSO_PUBLIC_API_ORIGIN": "https://api.example.test",
            "SSO_FRONTEND_ORIGIN": "https://ui.example.test",
            "SSO_ALLOWED_ORIGINS": ["https://sso.example.test"],
            "SSO_ALLOWED_RETURN_ROOTS": ["/", "/projects"],
        },
        environ={},
    )
    app = create_app(settings)
    redis, adapter, users = MemoryRedis(), CorporateDouble(), UserDouble()
    app.state.sso.sessions = RedisSessions(redis, settings.sso.namespace)
    app.state.sso.adapter, app.state.sso.users = adapter, users
    db_closes = []

    async def db():
        try:
            yield SimpleNamespace()
        finally:
            db_closes.append(True)

    app.dependency_overrides[get_db] = db
    monkeypatch.setattr(UserRepository, "get_active", users.get_active)
    monkeypatch.setattr(UserService, "read", users.read)

    @app.post("/test/business")
    async def business(user_id=Depends(get_current_user_id)):
        return {"id": str(user_id)}

    @app.get("/test/stream-scope")
    async def stream(user_id=Depends(get_stream_user_id, scope="function")):
        return {"id": str(user_id)}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://api.example.test"
    ) as client:
        yield SimpleNamespace(
            app=app,
            client=client,
            settings=settings,
            redis=redis,
            adapter=adapter,
            users=users,
            runtime=app.state.sso,
            db_closes=db_closes,
        )
    await app.state.sso.close()


async def login(h, **params):
    response = await h.client.get(
        "/api/v1/auth/login/sso", params=params, follow_redirects=False
    )
    assert response.status_code == 302, response.text
    return response


@pytest.mark.asyncio
async def test_login_me_and_fixed_cookie_fields(sso):
    h = sso
    response = await login(h, return_to="/projects")
    assert response.headers["location"] == "https://ui.example.test/projects"
    cookie = response.headers["set-cookie"]
    assert all(
        part in cookie
        for part in [
            "Secure",
            "HttpOnly",
            "SameSite=lax",
            "Path=/",
            "Max-Age=1800",
        ]
    )
    sid = h.client.cookies[h.settings.sso.cookie_name]
    assert len(sid) == 43 and all(sid not in key for key in h.redis.data)
    stored = json.loads(next(iter(h.redis.data.values()))[0])
    assert set(stored) == {"user_id", "csrf_token", "expires_at"}
    assert stored["user_id"] == str(h.users.id)
    me = await h.client.get("/api/v1/users/me")
    assert me.status_code == 200 and me.json()["user_id"] == "000123"
    assert (
        me.json()["role"] == "user"
        and me.json()["csrf_token"] == stored["csrf_token"]
    )
    assert me.headers["cache-control"] == "no-store"
    assert (
        h.redis.reads == 1
    )  # Both /me and Actor use the same cached dependency result.


@pytest.mark.asyncio
async def test_identity_headers_do_not_bypass_cookie_auth(sso):
    for headers in (
        {},
        {"X-User-Id": "admin"},
        {"Authorization": "Bearer admin"},
    ):
        assert (
            await sso.client.get("/api/v1/users/me", headers=headers)
        ).status_code == 401
    await login(sso)
    response = await sso.client.get(
        "/api/v1/users/me", headers={"X-User-Id": "admin"}
    )
    assert (
        response.status_code == 200 and response.json()["user_id"] == "000123"
    )


@pytest.mark.asyncio
async def test_sdk_redirect_and_docs_return_target(sso):
    sso.adapter.employee = None
    response = await login(sso, target="docs")
    assert response.headers["location"] == sso.adapter.url
    assert (
        sso.adapter.callback
        == "https://api.example.test/api/v1/auth/login/sso?return_to=%2F&target=docs"
    )
    assert "set-cookie" not in response.headers and not sso.redis.data
    sso.adapter.employee = VerifiedEmployee("000123", "홍길동")
    assert (await login(sso, target="docs")).headers[
        "location"
    ] == "https://api.example.test/docs"


@pytest.mark.parametrize(
    "destination",
    [
        "https://evil.test",
        "//evil.test",
        "/\\evil.test",
        "/other",
        "/projects-evil",
        "/projects/../other",
        "/projects/%2f%2fevil",
        "/projects#fragment",
        "/projects\r\nLocation:evil",
    ],
)
@pytest.mark.asyncio
async def test_reject_untrusted_frontend_return(sso, destination):
    assert (
        await sso.client.get(
            "/api/v1/auth/login/sso", params={"return_to": destination}
        )
    ).status_code == 400
    assert not sso.redis.data


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.test/login",
        "https://sso.example.test@evil.test/login",
        "http://sso.example.test/login",
        "javascript:alert(1)",
    ],
)
@pytest.mark.asyncio
async def test_reject_untrusted_sdk_login_url(sso, url):
    sso.adapter.employee = None
    sso.adapter.url = url
    assert (await sso.client.get("/api/v1/auth/login/sso")).status_code == 502


@pytest.mark.asyncio
async def test_csrf_and_logout_revocation(sso):
    await login(sso)
    token = (await sso.client.get("/api/v1/users/me")).json()["csrf_token"]
    for headers in (
        {},
        {"X-CSRF-Token": "wrong"},
        [(b"X-CSRF-Token", b"\xc3\xa9")],
    ):
        assert (
            await sso.client.post("/test/business", headers=headers)
        ).status_code == 403
    response = await sso.client.post(
        "/test/business", headers={"X-CSRF-Token": token}
    )
    assert response.status_code == 200 and response.json()["id"] == str(
        sso.users.id
    )
    assert sso.users.lock_flags[-1] is True
    assert (await sso.client.post("/api/v1/auth/logout")).status_code == 403
    response = await sso.client.post(
        "/api/v1/auth/logout", headers={"X-CSRF-Token": token}
    )
    assert response.status_code == 204 and not sso.redis.data
    assert (await sso.client.get("/api/v1/users/me")).status_code == 401


@pytest.mark.asyncio
async def test_latest_role_and_disabled_account_checked_in_db(sso):
    await login(sso)
    sso.users.role = UserRole.ADMIN
    assert (await sso.client.get("/api/v1/users/me")).json()["role"] == "admin"
    sso.users.active = False
    assert (await sso.client.get("/api/v1/users/me")).status_code == 401


@pytest.mark.asyncio
async def test_fixed_ttl_and_relogin_rotation(sso):
    await login(sso)
    old = sso.client.cookies[sso.settings.sso.cookie_name]
    await login(sso)
    assert sso.client.cookies[sso.settings.sso.cookie_name] != old
    assert await sso.runtime.sessions.read(old) is None
    assert len(sso.redis.data) == 1
    sso.redis.now = 1799
    assert (await sso.client.get("/api/v1/users/me")).status_code == 200
    sso.redis.now = 1800
    assert (await sso.client.get("/api/v1/users/me")).status_code == 401


@pytest.mark.asyncio
async def test_multi_instance_shares_session_and_namespace_isolates_services(
    sso,
):
    await login(sso)
    sid = sso.client.cookies[sso.settings.sso.cookie_name]
    same = RedisSessions(sso.redis, sso.settings.sso.namespace)
    other = RedisSessions(sso.redis, "other-service:dev:sso")
    assert (await same.read(sid)).user_id == str(sso.users.id)
    assert await other.read(sid) is None


@pytest.mark.asyncio
async def test_sdk_expiry_caps_local_session_and_rejects_expired(sso):
    sso.adapter.employee = replace(
        sso.adapter.employee, valid_until_epoch=int(time.time()) + 60
    )
    await login(sso)
    assert 0 < next(iter(sso.redis.data.values()))[1] <= 60
    sso.redis.data.clear()
    sso.adapter.employee = replace(
        sso.adapter.employee, valid_until_epoch=int(time.time()) - 1
    )
    assert (await sso.client.get("/api/v1/auth/login/sso")).status_code == 401
    assert not sso.redis.data


@pytest.mark.asyncio
async def test_sdk_and_redis_outages_fail_closed_and_hide_secrets(sso):
    sso.adapter.error = RuntimeError("sdk-private-secret")
    response = await sso.client.get("/api/v1/auth/login/sso")
    assert (
        response.status_code == 503
        and "sdk-private-secret" not in response.text
    )
    sso.adapter.error = None
    await login(sso)
    sso.redis.fail = True
    response = await sso.client.get(
        "/api/v1/users/me", headers={"X-User-Id": "admin"}
    )
    assert response.status_code == 503 and "private-url" not in response.text


@pytest.mark.asyncio
async def test_sdk_deadline_and_sync_thread_bridge(sso):
    class Slow:
        async def verify(self, request):
            await asyncio.sleep(1)

    sso.runtime.settings = sso.settings.sso.model_copy(
        update={"call_timeout_seconds": 0.001}
    )
    sso.runtime.adapter = Slow()
    assert (await sso.client.get("/api/v1/auth/login/sso")).status_code == 503
    threads = []

    def verify(request):
        threads.append(threading.current_thread().name)
        return VerifiedEmployee("000123", "홍길동")

    sso.runtime.settings = sso.settings.sso
    sso.runtime.adapter = SyncSsoAdapter(
        verify, lambda request, url: "https://sso.example.test/login"
    )
    await login(sso)
    assert threads and all("AnyIO worker thread" in name for name in threads)


@pytest.mark.asyncio
async def test_swagger_page_and_openapi_auth_metadata(sso):
    response = await sso.client.get("/docs")
    assert (
        response.status_code == 200
        and response.headers["cache-control"] == "no-store"
    )
    for text in (
        "sso-login",
        "ssoRequestInterceptor",
        "X-CSRF-Token",
        "url.origin !== window.location.origin",
        "persistAuthorization",
        "target=docs",
    ):
        assert text in response.text
    schema = sso.app.openapi()
    assert (
        schema["components"]["securitySchemes"]["LoginSession"]["name"]
        == sso.settings.sso.cookie_name
    )
    assert schema["paths"]["/api/v1/auth/logout"]["post"]["security"] == [
        {"LoginSession": []}
    ]
    assert not schema["paths"]["/api/v1/auth/login/sso"]["get"].get("security")
    assert not schema["paths"]["/health"]["get"].get("security")


def test_unconfigured_adapter_and_import_errors_have_no_header_fallback():
    assert type(load_adapter(SsoSettings())).__name__ == "UnconfiguredAdapter"
    with pytest.raises(ValueError, match="SSO_ADAPTER_FACTORY"):
        load_adapter(
            SsoSettings(adapter_factory="missing_private_company_sdk:create")
        )


@pytest.mark.parametrize(
    "fields",
    [
        {"cookie_samesite": "none", "cookie_secure": False},
        {"cookie_name": "__Host-test", "cookie_secure": False},
        {"public_api_origin": "https://user:secret@test"},
        {"public_api_origin": "https://test/path"},
        {"public_api_origin": "http://test"},
        {"allowed_return_roots": ["//evil"]},
        {"namespace": "name with spaces"},
        {"adapter_factory": "module:create()"},
        {"redis_timeout_seconds": float("inf")},
    ],
)
def test_invalid_sso_configuration_rejected(fields):
    with pytest.raises(ValueError):
        SsoSettings(**fields)


def test_central_sso_config_priority_and_profile_namespace():
    settings = service_settings.load_settings(
        config={"SSO_SESSION_TTL_SECONDS": 600},
        environ={
            "SSO_SESSION_TTL_SECONDS": "1200",
            "SSO_ALLOWED_ORIGINS": '["https://sso.test"]',
        },
    )
    assert (
        settings.sso.session_ttl_seconds == 600
        and settings.sso.allowed_origins == ("https://sso.test",)
    )
    assert settings.sources["SSO_SESSION_TTL_SECONDS"] == "config mapping"
    assert settings.sso.namespace == "dtest-agent:local:sso"
    with pytest.raises(service_settings.ConfigurationError) as exc:
        service_settings.load_settings(
            config={"SSO_PUBLIC_API_ORIGIN": "https://private:secret@test"},
            environ={},
        )
    assert "secret" not in str(exc.value)


@pytest.mark.asyncio
async def test_corrupt_session_is_not_treated_as_user_identity(sso):
    await login(sso)
    key = next(iter(sso.redis.data))
    sso.redis.data[key] = (
        json.dumps(
            {"user_id": "admin", "csrf_token": "bad", "expires_at": True}
        ),
        600,
    )
    assert (await sso.client.get("/api/v1/users/me")).status_code == 503


@pytest.mark.asyncio
async def test_workflow_search_cookie_csrf_and_short_identity_scope(
    sso, monkeypatch
):
    from unittest.mock import AsyncMock, Mock

    from sqlalchemy.ext.asyncio import AsyncSession

    from dtest.application.workflows import queries
    from dtest.infrastructure.database import runtime as database

    h = sso
    await login(h)
    me = await h.client.get("/api/v1/users/me")
    csrf = me.json()["csrf_token"]
    h.users.lock_flags.clear()
    db = AsyncMock(spec=AsyncSession)
    factory = Mock(return_value=db)
    monkeypatch.setattr(database, "get_session_factory", lambda: factory)

    async def search(query):
        db.close.assert_awaited_once()
        return {"items": [], "diagnostics": {"termination": "disabled"}}

    monkeypatch.setattr(queries, "search_workflows", search)
    response = await h.client.post(
        "/api/v1/workflows/search", json={"query": "analysis"}
    )
    assert response.status_code == 403
    factory.assert_not_called()
    response = await h.client.post(
        "/api/v1/workflows/search",
        json={"query": "analysis"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    assert h.users.lock_flags == [False]
    factory.assert_called_once()
    db.close.assert_awaited_once()
