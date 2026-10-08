from dtest.contracts.errors import ApplicationError

"""Opt-in real LOCAL Redis tests; create/delete only a random test namespace, never flush."""
import asyncio
import os
import secrets
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
import pytest_asyncio
from redis.asyncio import Redis

from dtest.infrastructure.redis.login_sessions import RedisSessions


@pytest_asyncio.fixture
async def redis_pair():
    url = os.getenv("DTEST_SSO_TEST_REDIS_URL")
    if not url:
        pytest.skip("Needs an explicitly selected local Redis")
    target = urlsplit(url)
    assert target.scheme in {"redis", "rediss"} and target.hostname in {
        "127.0.0.1",
        "localhost",
    }
    namespace = "dtest-agent:sso-test:" + uuid4().hex
    login = Redis.from_url(
        url, decode_responses=True, max_connections=1, socket_timeout=2
    )
    streams = Redis.from_url(
        url, decode_responses=True, max_connections=1, socket_timeout=10
    )
    store = RedisSessions(login, namespace)
    cleanup = []
    try:
        await login.ping()
        yield login, streams, store, namespace, cleanup
    finally:
        for key in cleanup:
            assert key.startswith(namespace + ":")
            await login.delete(key)
        await login.aclose()
        await streams.aclose()


@pytest.mark.asyncio
async def test_real_redis_stream_and_login_records_coexist(redis_pair):
    login, streams, store, namespace, cleanup = redis_pair
    stream = namespace + ":events"
    cleanup.append(stream)
    await streams.xadd(stream, {"type": "test.event"})
    sid, session = await store.create("test-user-id", 60)
    key = store._key(sid)
    cleanup.append(key)
    assert (
        await login.type(stream) == "stream"
        and await login.type(key) == "string"
    )
    assert 0 < await login.ttl(key) <= 60
    assert (await store.read(sid)) == session
    other_process = RedisSessions(streams, namespace)
    assert await other_process.read(sid) == session
    assert await RedisSessions(login, namespace + ":other").read(sid) is None
    await store.revoke(sid)
    assert await store.read(sid) is None
    assert await streams.xlen(stream) == 1


@pytest.mark.asyncio
async def test_blocking_stream_pool_does_not_occupy_login_pool(redis_pair):
    login, streams, store, namespace, cleanup = redis_pair
    stream = namespace + ":events"
    cleanup.append(stream)
    await streams.xgroup_create(stream, "test-group", id="$", mkstream=True)
    sid, _ = await store.create("test-user-id", 60)
    cleanup.append(store._key(sid))
    blocking = asyncio.create_task(
        streams.xreadgroup(
            "test-group", "test-consumer", {stream: ">"}, block=5000, count=1
        )
    )
    try:
        await asyncio.sleep(0.05)
        assert not blocking.done()
        assert (
            await asyncio.wait_for(store.read(sid), 1)
        ).user_id == "test-user-id"
    finally:
        blocking.cancel()
        await asyncio.gather(blocking, return_exceptions=True)


@pytest.mark.asyncio
async def test_owned_login_pool_waits_with_bounded_capacity_and_timeout(
    redis_pair,
):
    """A burst waits for capacity; exhausted deadlines still fail closed."""
    from fastapi import FastAPI, HTTPException
    from redis.asyncio import BlockingConnectionPool

    from dtest.api_service.auth.runtime import attach_sso
    from dtest.settings.auth import SsoSettings

    login, streams, store, namespace, cleanup = redis_pair
    runtime = attach_sso(
        FastAPI(),
        settings=SsoSettings(
            namespace=namespace + ":bounded",
            redis_max_connections=1,
            redis_timeout_seconds=0.2,
        ),
        users=object(),
        adapter=object(),
        redis_url=os.environ["DTEST_SSO_TEST_REDIS_URL"],
        api_prefix="/api/v1",
        docs_path="/docs",
    )
    client = runtime._owned_redis
    pool = client.connection_pool
    assert (
        isinstance(pool, BlockingConnectionPool) and pool.max_connections == 1
    )
    sid, expected = await runtime.sessions.create("bounded-user", 60)
    key = runtime.sessions._key(sid)
    try:
        held = await pool.get_connection()
        pending = asyncio.create_task(runtime.sessions.read(sid))
        await asyncio.sleep(0.025)
        assert not pending.done()
        await pool.release(held)
        assert await asyncio.wait_for(pending, 1) == expected
        held = await pool.get_connection()
        try:
            with pytest.raises((HTTPException, ApplicationError)) as rejected:
                await asyncio.wait_for(runtime.sessions.read(sid), 1)
            assert rejected.value.status_code == 503
        finally:
            await pool.release(held)
        assert await runtime.sessions.read(sid) == expected
    finally:
        await client.delete(key)
        await runtime.close()
        assert not pool._in_use_connections and all(
            not c.is_connected for c in pool._available_connections
        )


@pytest.mark.asyncio
async def test_real_flow_is_shared_and_consumed_once_across_clients(
    redis_pair,
):
    from dtest.infrastructure.redis.login_flows import RedisLoginFlows

    login, streams, _, namespace, cleanup = redis_pair
    first = RedisLoginFlows(login, namespace)
    second = RedisLoginFlows(streams, namespace)
    token = secrets.token_urlsafe(32)
    await first.create(token, "/projects?tab=a&sort=b", "app", 60)
    key = first._key(token)
    assert key is not None
    cleanup.append(key)
    assert token not in key and 0 < await login.ttl(key) <= 60
    outcomes = await asyncio.gather(
        first.consume(token), second.consume(token)
    )
    assert sum(outcome is not None for outcome in outcomes) == 1
    flow = next(outcome for outcome in outcomes if outcome is not None)
    assert flow.return_to == "/projects?tab=a&sort=b" and flow.target == "app"
    assert await login.exists(key) == 0


@pytest.mark.asyncio
async def test_real_flow_expires_without_a_callback(redis_pair):
    from dtest.infrastructure.redis.login_flows import RedisLoginFlows

    login, _, _, namespace, cleanup = redis_pair
    store = RedisLoginFlows(login, namespace)
    token = secrets.token_urlsafe(32)
    await store.create(token, "/", "docs", 1)
    key = store._key(token)
    assert key is not None
    cleanup.append(key)
    await asyncio.sleep(1.1)
    assert await login.exists(key) == 0
    assert await store.consume(token) is None


@pytest.mark.asyncio
async def test_real_flow_and_login_with_same_token_do_not_collide(redis_pair):
    from dtest.infrastructure.redis.login_flows import RedisLoginFlows

    login, _, sessions, namespace, cleanup = redis_pair
    flows = RedisLoginFlows(login, namespace)
    sid, expected = await sessions.create("flow-user", 60)
    await flows.create(sid, "/", "app", 60)
    cleanup.extend([sessions._key(sid), flows._key(sid)])
    flow = await flows.consume(sid)
    assert flow is not None and flow.return_to == "/"
    assert await sessions.read(sid) == expected


@pytest.mark.asyncio
async def test_real_flow_callback_can_land_on_another_api_instance(redis_pair):
    import httpx
    from fastapi import FastAPI

    from dtest.api_service.auth.runtime import attach_sso
    from dtest.settings.auth import SsoSettings
    from tests.api_service.test_sso_auth import CorporateDouble, UserDouble

    login, streams, _, namespace, cleanup = redis_pair
    settings = SsoSettings(
        public_api_origin="http://api.example.test",
        frontend_origin="http://ui.example.test",
        allowed_origins=("https://sso.example.test",),
        allowed_return_roots=("/projects",),
        namespace=namespace,
        cookie_secure=False,
    )
    api_origin = settings.public_api_origin
    assert api_origin is not None
    users = UserDouble()
    first_sdk, second_sdk = CorporateDouble(), CorporateDouble()
    first_sdk.employee = None
    first_app, second_app = FastAPI(), FastAPI()
    first = attach_sso(
        first_app,
        settings=settings,
        users=users,
        adapter=first_sdk,
        redis=login,
        redis_url="redis://unused",
        api_prefix="/api/v1",
        docs_path="/docs",
    )
    second = attach_sso(
        second_app,
        settings=settings,
        users=users,
        adapter=second_sdk,
        redis=streams,
        redis_url="redis://unused",
        api_prefix="/api/v1",
        docs_path="/docs",
    )
    async with (
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=first_app),
            base_url=api_origin,
        ) as start,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=second_app),
            base_url=api_origin,
        ) as complete,
    ):
        response = await start.get(
            "/api/v1/auth/login/sso", params={"return_to": "/projects"}
        )
        assert response.status_code == 302
        callback = first_sdk.callback
        assert isinstance(callback, str)
        token = urlsplit(callback).path.rsplit("/", 1)[1]
        cleanup.append(first.flows._key(token))
        response = await complete.get(callback)
        assert response.status_code == 302
        assert (
            response.headers["location"] == "http://ui.example.test/projects"
        )
        sid = complete.cookies[settings.cookie_name]
        cleanup.append(second.sessions._key(sid))
        session = await first.sessions.read(sid)
        assert session is not None and session.user_id == str(users.id)
        assert await first.flows.consume(token) is None
        assert (await complete.get(callback)).status_code == 400
