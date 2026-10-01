"""Opaque browser SID, hashed Redis key, fixed TTL. No roles, SSO cookies or employee PII."""
import hashlib
import json
import re
import secrets
import time
from dataclasses import asdict, dataclass
from typing import Protocol

from fastapi import HTTPException
from redis.exceptions import RedisError


class RedisCommands(Protocol):
    async def set(self, key: str, value: str, *, ex: int, nx: bool): ...
    async def get(self, key: str): ...
    async def delete(self, key: str): ...


@dataclass(frozen=True)
class LoginSession:
    user_id: str
    csrf_token: str
    expires_at: int


class RedisSessions:
    def __init__(self, redis: RedisCommands, namespace: str):
        self.redis, self.namespace = redis, namespace

    def _key(self, sid: str) -> str | None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", sid):
            return None
        return f"{self.namespace}:login:{hashlib.sha256(sid.encode()).hexdigest()}"

    async def create(self, user_id: str, ttl: int) -> tuple[str, LoginSession]:
        if ttl <= 0:
            raise ValueError("Session TTL must be positive")
        session = LoginSession(user_id, secrets.token_urlsafe(32), int(time.time()) + ttl)
        try:
            for _ in range(3):
                sid = secrets.token_urlsafe(32)
                if await self.redis.set(self._key(sid), json.dumps(asdict(session)), ex=ttl, nx=True):
                    return sid, session
        except RedisError:
            raise HTTPException(503, "Login session storage is unavailable.") from None
        raise HTTPException(503, "Cannot create login session.")

    async def read(self, sid: str) -> LoginSession | None:
        key = self._key(sid)
        if key is None:
            return None
        try:
            raw = await self.redis.get(key)
        except RedisError:
            raise HTTPException(503, "Login session storage is unavailable.") from None
        if raw is None:
            return None
        try:
            if len(raw) > 2048:
                raise ValueError()
            session = LoginSession(**json.loads(raw))
            if (not isinstance(session.user_id, str) or not session.user_id
                    or not isinstance(session.csrf_token, str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{43}", session.csrf_token)
                    or type(session.expires_at) is not int):
                raise ValueError()
            return session if session.expires_at > time.time() else None
        except (ValueError, TypeError, UnicodeError):
            raise HTTPException(503, "Invalid login session record.") from None

    async def revoke(self, sid: str) -> None:
        key = self._key(sid)
        if key is not None:
            try:
                await self.redis.delete(key)
            except RedisError:
                raise HTTPException(503, "Login session storage is unavailable.") from None
