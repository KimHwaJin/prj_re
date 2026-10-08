"""HTTP contracts on a plain FastAPI app, without project handlers or DB."""

import time
from typing import Any
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI, Request

from conftest import Harness, Users
from sso import SsoSettings, VerifiedEmployee, attach_sso


async def login(h: Harness) -> dict[str, Any]:
    response = await h.client.get("/api/v1/auth/login/sso")
    assert response.status_code == 302, response.text
    assert response.headers["location"] == "https://ui.example.test/"
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "Secure" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]
    session = await h.client.get("/api/v1/auth/session")
    assert session.headers["cache-control"] == "no-store"
    assert session.status_code == 200
    return session.json()


async def test_login_session_csrf_business_and_logout(h: Harness):
    assert (await h.client.get("/api/v1/auth/session")).status_code == 401
    assert (await h.client.post("/business")).status_code == 401
    record = await login(h)
    assert set(record) == {"user_id", "csrf_token", "expires_at"}
    assert record["user_id"] == "internal-user-id"
    assert record["expires_at"] > time.time()
    assert "hong@example.test" not in str(h.redis.data)
    assert (await h.client.post("/business")).status_code == 403
    headers = {"X-CSRF-Token": record["csrf_token"]}
    assert (await h.client.post("/business", headers=headers)).json() == {
        "user_id": "internal-user-id"
    }
    assert (await h.client.post("/api/v1/auth/logout")).status_code == 403
    response = await h.client.post("/api/v1/auth/logout", headers=headers)
    assert response.status_code == 204
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert not h.redis.data
    assert (await h.client.get("/api/v1/auth/session")).status_code == 401


async def test_callback_preserves_target_and_rejects_replay(h: Harness):
    h.adapter.employee = None
    response = await h.client.get(
        "/api/v1/auth/login/sso",
        params={"return_to": "/projects?tab=a&sort=b"},
    )
    assert response.status_code == 302
    callback = h.adapter.callback
    assert callback is not None and not urlsplit(callback).query
    assert response.headers["location"] == h.adapter.url
    assert not h.users.employees
    h.adapter.employee = VerifiedEmployee("000123", "홍길동")
    response = await h.client.get(
        callback, params={"return_to": "//evil.test", "target": "docs"}
    )
    assert response.headers["location"] == (
        "https://ui.example.test/projects?tab=a&sort=b"
    )
    assert (await h.client.get(callback)).status_code == 400
    assert (await h.client.get("/api/v1/auth/session")).status_code == 200


async def test_unverified_callback_ends_without_redirect(h: Harness):
    h.adapter.employee = None
    await h.client.get("/api/v1/auth/login/sso")
    assert h.adapter.callback is not None
    response = await h.client.get(h.adapter.callback)
    assert response.status_code == 401
    assert "location" not in response.headers
    assert not h.redis.data and not h.users.employees


@pytest.mark.parametrize("token", ["missing", "invalid!", "a" * 43])
async def test_invalid_flow_does_not_call_sdk(h: Harness, token: str):
    h.adapter.error = RuntimeError("SDK must not be called")
    response = await h.client.get("/api/v1/auth/login/sso/callback/" + token)
    assert response.status_code == 400


async def test_expired_flow(h: Harness):
    h.adapter.employee = None
    await h.client.get("/api/v1/auth/login/sso")
    assert h.adapter.callback is not None
    h.redis.now = 300
    assert (await h.client.get(h.adapter.callback)).status_code == 400


async def test_sdk_direct_callback_does_not_authenticate(
    h: Harness, monkeypatch: pytest.MonkeyPatch
):
    async def direct(request: Request, return_url: str) -> str:
        h.adapter.callback = return_url
        return return_url

    h.adapter.employee = None
    monkeypatch.setattr(h.adapter, "login_url", direct)
    response = await h.client.get("/api/v1/auth/login/sso")
    assert response.status_code == 302
    assert response.headers["location"] == h.adapter.callback
    assert (
        await h.client.get(response.headers["location"])
    ).status_code == 401
    assert (await h.client.get("/api/v1/auth/session")).status_code == 401


@pytest.mark.parametrize(
    "return_to",
    [
        "//evil.test",
        "https://evil.test",
        "/demo/../a",
        "/%2fdemo",
        "/demo#token",
        "/missing",
        "/demo\\evil",
        "/demo",
    ],
)
async def test_return_path_is_validated_before_sdk(
    h: Harness,
    return_to: str,
):
    h.adapter.error = RuntimeError("SDK must not be called")
    response = await h.client.get(
        "/api/v1/auth/login/sso", params={"return_to": return_to}
    )
    assert response.status_code == 400
    assert not h.redis.data


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.test",
        "https://sso.example.test@evil.test/login",
        "http://sso.example.test/login",
        "https://api.example.test/demo",
        "https://name:secret@sso.example.test/login",
        "https://sso.example.test/login secret",
    ],
)
async def test_sdk_url_allowlist(h: Harness, url: str):
    h.adapter.employee = None
    h.adapter.url = url
    response = await h.client.get("/api/v1/auth/login/sso")
    assert response.status_code == 502
    assert not h.redis.data


async def test_sdk_error_sanitized(
    h: Harness, caplog: pytest.LogCaptureFixture
):
    h.adapter.error = RuntimeError("private-sdk-secret")
    response = await h.client.get("/api/v1/auth/login/sso")
    assert response.status_code == 503
    assert "private-sdk-secret" not in response.text + caplog.text
    assert not h.users.employees


async def test_redis_errors_are_http_on_plain_fastapi(h: Harness):
    h.redis.fail = True
    response = await h.client.get("/api/v1/auth/login/sso")
    assert response.status_code == 503
    assert "private-redis-secret" not in response.text
    h.adapter.employee = None
    assert (await h.client.get("/api/v1/auth/login/sso")).status_code == 503


async def test_cookie_rotation_expiry_and_corrupt_session(h: Harness):
    await login(h)
    old = h.client.cookies["example_session"]
    await login(h)
    assert h.client.cookies["example_session"] != old
    assert await h.runtime.sessions.read(old) is None
    key = next(iter(h.redis.data))
    h.redis.data[key] = ('{"invalid":true}', 1800)
    assert (await h.client.get("/api/v1/auth/session")).status_code == 503
    h.redis.data.clear()
    await login(h)
    h.redis.now = 1800
    assert (await h.client.get("/api/v1/auth/session")).status_code == 401


async def test_expired_employee_not_registered(h: Harness):
    h.adapter.employee = VerifiedEmployee(
        "000123", "홍길동", valid_until_epoch=int(time.time()) - 1
    )
    assert (await h.client.get("/api/v1/auth/login/sso")).status_code == 401
    assert not h.users.employees and not h.redis.data


async def test_invalid_user_binding_no_cookie(h: Harness):
    h.users.result = ""
    assert (await h.client.get("/api/v1/auth/login/sso")).status_code == 502
    assert not h.redis.data


async def test_sdk_timeout(h: Harness, monkeypatch: pytest.MonkeyPatch):
    import asyncio

    async def slow(request: Request) -> VerifiedEmployee | None:
        await asyncio.sleep(1)
        return None

    h.runtime.settings = h.runtime.settings.model_copy(
        update={"call_timeout_seconds": 0.01}
    )
    monkeypatch.setattr(h.adapter, "verify", slow)
    assert (await h.client.get("/api/v1/auth/login/sso")).status_code == 503


async def test_attach_preserves_existing_lifespan_and_handlers(h: Harness):
    lifespan = h.app.router.lifespan_context
    handlers = dict(h.app.exception_handlers)
    with pytest.raises(RuntimeError, match="already attached"):
        attach_sso(
            h.app,
            settings=h.runtime.settings,
            users=h.users,
            redis_url="redis://unused",
            redis=h.redis,
            adapter=h.adapter,
        )
    assert h.app.router.lifespan_context is lifespan
    assert h.app.exception_handlers == handlers
    assert (await h.client.get("/openapi.json")).status_code == 200


@pytest.mark.parametrize("path", ["relative", "//evil", "/a/../b", "/a?b"])
def test_attach_invalid_prefix_before_mutating_app(path: str):
    app = FastAPI()
    settings = SsoSettings(namespace="a:sso", cookie_name="a_session")
    with pytest.raises(ValueError, match="absolute paths"):
        attach_sso(
            app,
            settings=settings,
            users=Users(),
            redis_url="redis://unused",
            api_prefix=path,
        )
    assert not hasattr(app.state, "sso")


@pytest.mark.parametrize(
    "values",
    [
        {"public_api_origin": "https://api.test/"},
        {"frontend_origin": "http://ui.test"},
        {"allowed_origins": ("https://a.test?token=secret",)},
        {"namespace": "bad space"},
        {"cookie_name": "bad name"},
        {"cookie_secure": False, "cookie_samesite": "none"},
        {"login_flow_ttl_seconds": 59},
        {"session_ttl_seconds": 0},
        {"call_timeout_seconds": float("inf")},
        {"allowed_return_roots": ("/../a",)},
    ],
)
def test_settings_boundaries(values: dict[str, Any]):
    with pytest.raises(ValueError):
        SsoSettings.model_validate(
            {"namespace": "a:sso", "cookie_name": "a_session", **values}
        )


async def test_legacy_callback_marker_does_not_prove_identity(h: Harness):
    h.adapter.employee = None
    response = await h.client.get(
        "/api/v1/auth/login/sso", params={"sso_callback": "true"}
    )
    assert response.status_code == 401 and not h.redis.data
