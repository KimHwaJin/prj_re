from dtest.contracts.errors import ApplicationError

"""Opt-in real LOCAL Redis tests; create/delete only a random test namespace, never flush."""
import asyncio
import os
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
    from dtest.api_service.auth.runtime import attach_sso
    from dtest.settings.auth import SsoSettings
    from redis.asyncio import BlockingConnectionPool

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
