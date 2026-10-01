"""Opt-in real LOCAL Redis tests; create/delete only a random test namespace, never flush."""
import asyncio
import os
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
import pytest_asyncio
from redis.asyncio import Redis

from service_auth.sso.sessions import RedisSessions


@pytest_asyncio.fixture
async def redis_pair():
    url=os.getenv("DTEST_SSO_TEST_REDIS_URL")
    if not url:
        pytest.skip("Needs an explicitly selected local Redis")
    target=urlsplit(url)
    assert target.scheme in {"redis","rediss"} and target.hostname in {"127.0.0.1","localhost"}
    namespace="dtest-agent:sso-test:"+uuid4().hex
    login=Redis.from_url(url,decode_responses=True,max_connections=1,socket_timeout=2)
    streams=Redis.from_url(url,decode_responses=True,max_connections=1,socket_timeout=10)
    store=RedisSessions(login,namespace)
    cleanup=[]
    try:
        await login.ping()
        yield login,streams,store,namespace,cleanup
    finally:
        for key in cleanup:
            assert key.startswith(namespace+":")
            await login.delete(key)
        await login.aclose()
        await streams.aclose()


@pytest.mark.asyncio
async def test_real_redis_stream_and_login_records_coexist(redis_pair):
    login,streams,store,namespace,cleanup=redis_pair
    stream=namespace+":events"; cleanup.append(stream)
    await streams.xadd(stream,{"type":"test.event"})
    sid,session=await store.create("test-user-id",60)
    key=store._key(sid); cleanup.append(key)
    assert await login.type(stream)=="stream" and await login.type(key)=="string"
    assert 0<await login.ttl(key)<=60
    assert (await store.read(sid))==session
    other_process=RedisSessions(streams,namespace)
    assert await other_process.read(sid)==session
    assert await RedisSessions(login,namespace+":other").read(sid) is None
    await store.revoke(sid)
    assert await store.read(sid) is None
    assert await streams.xlen(stream)==1


@pytest.mark.asyncio
async def test_blocking_stream_pool_does_not_occupy_login_pool(redis_pair):
    login,streams,store,namespace,cleanup=redis_pair
    stream=namespace+":events"; cleanup.append(stream)
    await streams.xgroup_create(stream,"test-group",id="$",mkstream=True)
    sid,_=await store.create("test-user-id",60); cleanup.append(store._key(sid))
    blocking=asyncio.create_task(streams.xreadgroup("test-group","test-consumer",{stream:">"},block=5000,count=1))
    try:
        await asyncio.sleep(.05)
        assert not blocking.done()
        assert (await asyncio.wait_for(store.read(sid),1)).user_id=="test-user-id"
    finally:
        blocking.cancel()
        await asyncio.gather(blocking,return_exceptions=True)
