"""Single-use SSO return context on the existing login Redis pool."""

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass
from typing import Literal

from redis.exceptions import RedisError

from sso.errors import SsoError
from sso.storage.backend import RedisBackend

# GET and DEL must be atomic across API replicas; no GETDEL version dependency.
_CONSUME = """
local value = redis.call('GET', KEYS[1])
redis.call('DEL', KEYS[1])
return value
"""


@dataclass(frozen=True)
class LoginFlow:
    return_to: str
    target: Literal["app", "docs"]
    expires_at: int


class RedisLoginFlows:
    def __init__(self, redis: RedisBackend, namespace: str):
        self.redis, self.namespace = redis, namespace

    def _key(self, token: str) -> str | None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        return f"{self.namespace}:flow:{digest}"

    async def create(
        self,
        token: str,
        return_to: str,
        target: Literal["app", "docs"],
        ttl: int,
    ) -> None:
        key = self._key(token)
        if key is None or ttl <= 0 or target not in {"app", "docs"}:
            raise ValueError("Invalid SSO login flow")
        flow = LoginFlow(return_to, target, int(time.time()) + ttl)
        try:
            created = await self.redis.execute_command(
                "SET", key, json.dumps(asdict(flow)), "EX", ttl, "NX"
            )
        except RedisError:
            raise SsoError(
                503, "SSO login flow storage is unavailable."
            ) from None
        if not created:
            raise SsoError(503, "Cannot create SSO login flow.")

    async def consume(self, token: str) -> LoginFlow | None:
        key = self._key(token)
        if key is None:
            return None
        try:
            raw = await self.redis.execute_command("EVAL", _CONSUME, 1, key)
        except RedisError:
            raise SsoError(
                503, "SSO login flow storage is unavailable."
            ) from None
        if raw is None:
            return None
        try:
            flow = LoginFlow(**json.loads(raw))
            if (
                not isinstance(flow.return_to, str)
                or not flow.return_to
                or flow.target not in {"app", "docs"}
                or type(flow.expires_at) is not int
            ):
                raise ValueError()
            return flow if flow.expires_at > time.time() else None
        except (ValueError, TypeError, UnicodeError):
            raise SsoError(503, "Invalid SSO login flow record.") from None
