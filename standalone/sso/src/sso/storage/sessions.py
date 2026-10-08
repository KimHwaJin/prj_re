"""Opaque SID with hashed Redis key and fixed TTL; no employee profile."""

import hashlib
import json
import re
import secrets
import time
from dataclasses import asdict, dataclass

from redis.exceptions import RedisError

from sso.errors import SsoError
from sso.storage.backend import RedisBackend


@dataclass(frozen=True)
class LoginSession:
    user_id: str
    csrf_token: str
    expires_at: int


class RedisSessions:
    def __init__(self, redis: RedisBackend, namespace: str):
        self.redis, self.namespace = redis, namespace

    def _key(self, sid: str) -> str | None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", sid):
            return None
        digest = hashlib.sha256(sid.encode()).hexdigest()
        return f"{self.namespace}:login:{digest}"

    async def create(self, user_id: str, ttl: int) -> tuple[str, LoginSession]:
        if ttl <= 0:
            raise ValueError("Session TTL must be positive")
        session = LoginSession(
            user_id, secrets.token_urlsafe(32), int(time.time()) + ttl
        )
        try:
            for _ in range(3):
                sid = secrets.token_urlsafe(32)
                if await self.redis.execute_command(
                    "SET",
                    self._key(sid),
                    json.dumps(asdict(session)),
                    "EX",
                    ttl,
                    "NX",
                ):
                    return sid, session
        except RedisError:
            raise SsoError(
                503, "Login session storage is unavailable."
            ) from None
        raise SsoError(503, "Cannot create login session.")

    async def read(self, sid: str) -> LoginSession | None:
        key = self._key(sid)
        if key is None:
            return None
        try:
            raw = await self.redis.execute_command("GET", key)
        except RedisError:
            raise SsoError(
                503, "Login session storage is unavailable."
            ) from None
        if raw is None:
            return None
        try:
            if len(raw) > 2048:
                raise ValueError()
            session = LoginSession(**json.loads(raw))
            if (
                not isinstance(session.user_id, str)
                or not session.user_id
                or not isinstance(session.csrf_token, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{43}", session.csrf_token)
                or type(session.expires_at) is not int
            ):
                raise ValueError()
            return session if session.expires_at > time.time() else None
        except (ValueError, TypeError, UnicodeError):
            raise SsoError(503, "Invalid login session record.") from None

    async def revoke(self, sid: str) -> None:
        key = self._key(sid)
        if key is not None:
            try:
                await self.redis.execute_command("DEL", key)
            except RedisError:
                raise SsoError(
                    503, "Login session storage is unavailable."
                ) from None
