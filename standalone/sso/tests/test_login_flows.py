"""Single-use return context is separate from authenticated login sessions."""

import json
import secrets
import time

import pytest

from conftest import MemoryRedis
from sso.errors import SsoError
from sso.storage.login_flows import RedisLoginFlows
from sso.storage.sessions import RedisSessions


@pytest.mark.asyncio
async def test_flow_key_is_hashed_namespaced_and_separate_from_login():
    redis = MemoryRedis()
    store = RedisLoginFlows(redis, "test:sso")
    token = secrets.token_urlsafe(32)
    await store.create(token, "/projects?tab=a&sort=b", "app", 60)
    key = store._key(token)
    assert key is not None
    assert token not in key and key.startswith("test:sso:flow:")
    assert redis.data[key][1] == 60
    assert set(json.loads(redis.data[key][0])) == {
        "return_to",
        "target",
        "expires_at",
    }
    assert await RedisSessions(redis, "test:sso").read(token) is None
    assert await RedisLoginFlows(redis, "other:sso").consume(token) is None
    flow = await store.consume(token)
    assert flow is not None
    assert flow.return_to == "/projects?tab=a&sort=b" and flow.target == "app"
    assert not redis.data
    assert await store.consume(token) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "record",
    [
        "not-json",
        '{"return_to":"/","target":"app"}',
        '{"return_to":null,"target":"app","expires_at":9999999999}',
        '{"return_to":"/","target":[],"expires_at":9999999999}',
        '{"return_to":"/","target":"app","expires_at":true}',
    ],
)
async def test_corrupt_flow_is_consumed_and_fails_closed(record):
    redis = MemoryRedis()
    store = RedisLoginFlows(redis, "test:sso")
    token = secrets.token_urlsafe(32)
    await store.create(token, "/", "app", 60)
    key = store._key(token)
    assert key is not None
    redis.data[key] = (record, 60)
    with pytest.raises(SsoError) as error:
        await store.consume(token)
    assert error.value.status_code == 503
    assert not redis.data


@pytest.mark.asyncio
async def test_expired_record_is_rejected_even_if_redis_ttl_has_not_elapsed():
    redis = MemoryRedis()
    store = RedisLoginFlows(redis, "test:sso")
    token = secrets.token_urlsafe(32)
    await store.create(token, "/", "docs", 60)
    key = store._key(token)
    assert key is not None
    record = json.loads(redis.data[key][0])
    record["expires_at"] = int(time.time()) - 1
    redis.data[key] = (json.dumps(record), 60)
    assert await store.consume(token) is None
    assert not redis.data


@pytest.mark.asyncio
async def test_flow_collision_does_not_overwrite_previous_target():
    redis = MemoryRedis()
    store = RedisLoginFlows(redis, "test:sso")
    token = secrets.token_urlsafe(32)
    await store.create(token, "/projects", "app", 60)
    with pytest.raises(SsoError) as error:
        await store.create(token, "/", "docs", 60)
    assert error.value.status_code == 503
    flow = await store.consume(token)
    assert flow is not None and flow.return_to == "/projects"
