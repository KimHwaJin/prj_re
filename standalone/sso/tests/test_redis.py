"""Opt-in local Redis only; isolated random keys, no flush or shared groups."""

import asyncio
import os
import secrets
from collections.abc import AsyncIterator
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from redis.asyncio import Redis

from conftest import CorporateDouble, Users
from sso import SsoSettings, attach_sso
from sso.storage.login_flows import RedisLoginFlows
from sso.storage.sessions import RedisSessions


@pytest_asyncio.fixture
async def pair() -> AsyncIterator[tuple[Redis, Redis, str]]:
    url = os.getenv("SSO_TEST_REDIS_URL")
    if not url:
        pytest.skip(
            "Set SSO_TEST_REDIS_URL to an explicitly chosen local Redis"
        )
    assert urlsplit(url).hostname in {"127.0.0.1", "localhost"}
    namespace = "standalone-sso:test:" + uuid4().hex
    first = Redis.from_url(url, decode_responses=True, socket_timeout=2)
    second = Redis.from_url(url, decode_responses=True, socket_timeout=2)
    try:
        await first.ping()
        yield first, second, namespace
    finally:
        async for key in first.scan_iter(match=namespace + ":*"):
            await first.delete(key)
        await first.aclose()
        await second.aclose()


async def test_atomic_consume_and_key_separation(pair):
    first, second, namespace = pair
    flow = RedisLoginFlows(first, namespace)
    token = secrets.token_urlsafe(32)
    await flow.create(token, "/demo", "app", 60)
    assert await RedisSessions(second, namespace).read(token) is None
    results = await asyncio.gather(
        flow.consume(token), RedisLoginFlows(second, namespace).consume(token)
    )
    assert sum(result is not None for result in results) == 1


async def test_ttl_expiration(pair):
    first, second, namespace = pair
    flow = RedisLoginFlows(first, namespace)
    token = secrets.token_urlsafe(32)
    await flow.create(token, "/demo", "app", 1)
    await asyncio.sleep(1.1)
    assert await RedisLoginFlows(second, namespace).consume(token) is None


async def test_cross_app_roundtrip_and_borrowed_client_ownership(pair):
    first, second, namespace = pair
    adapter, users = CorporateDouble(), Users()
    adapter.employee = None
    settings = SsoSettings(
        namespace=namespace,
        cookie_name="test_session",
        public_api_origin="https://api.example.test",
        frontend_origin="https://ui.example.test",
        allowed_origins=("https://sso.example.test",),
        allowed_return_roots=("/demo",),
    )
    apps = [FastAPI(), FastAPI()]
    runtimes = [
        attach_sso(
            app,
            settings=settings,
            users=users,
            adapter=adapter,
            redis=client,
            redis_url="redis://unused",
        )
        for app, client in zip(apps, (first, second), strict=True)
    ]
    async with (
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=apps[0]),
            base_url="https://api.example.test",
        ) as a,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=apps[1]),
            base_url="https://api.example.test",
        ) as b,
    ):
        assert (
            await a.get(
                "/api/v1/auth/login/sso", params={"return_to": "/demo"}
            )
        ).status_code == 302
        from sso import VerifiedEmployee

        adapter.employee = VerifiedEmployee("000123", "홍길동")
        assert adapter.callback is not None
        response = await b.get(adapter.callback)
        assert response.headers["location"] == "https://ui.example.test/demo"
        a.cookies.update(b.cookies)
        assert (await a.get("/api/v1/auth/session")).status_code == 200
        assert (await a.get(adapter.callback)).status_code == 400
    for runtime in runtimes:
        await runtime.close()
    assert await first.ping() and await second.ping()


async def test_owned_client_close(pair):
    _, _, namespace = pair
    url = os.environ["SSO_TEST_REDIS_URL"]
    runtime = attach_sso(
        FastAPI(),
        settings=SsoSettings(
            namespace=namespace,
            cookie_name="test_session",
        ),
        users=Users(),
        adapter=CorporateDouble(),
        redis_url=url,
    )
    owned = runtime._owned_redis
    assert owned is not None
    await owned.ping()
    pool = owned.connection_pool
    await runtime.close()
    assert not pool._in_use_connections
    assert all(not item.is_connected for item in pool._available_connections)
