import asyncio
import logging
import secrets
import time
from typing import Literal
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from redis.asyncio import BlockingConnectionPool, Redis

from dtest.api_service.auth.dependencies import LoginDependency
from dtest.contracts.auth import SsoAdapter, UserDirectory, VerifiedEmployee
from dtest.infrastructure.redis.login_sessions import (
    LoginSession,
    RedisSessions,
)
from dtest.infrastructure.sso.adapter import load_adapter
from dtest.settings.auth import SsoSettings, origin

log = logging.getLogger(__name__)


def _diagnostic_identifier(value: object) -> str:
    """Bound names only; arbitrary SDK values must not become log payloads."""
    if isinstance(value, str) and len(value) <= 128 and value.isidentifier():
        return value
    return "unknown"


class SsoRuntime:
    def __init__(
        self,
        settings: SsoSettings,
        adapter: SsoAdapter,
        users: UserDirectory,
        sessions: RedisSessions,
        *,
        api_prefix: str,
        docs_path: str,
        owned_redis: Redis | None = None,
    ):
        self.settings, self.adapter, self.users, self.sessions = (
            settings,
            adapter,
            users,
            sessions,
        )
        self.api_prefix, self.docs_path = api_prefix, docs_path
        self._owned_redis = owned_redis

    def _destination(self, return_to: str, target: str) -> str:
        config = self.settings
        if not config.public_api_origin or not config.frontend_origin:
            raise HTTPException(
                503, "SSO public API and frontend origins are not configured."
            )
        if target == "docs":
            return config.public_api_origin + self.docs_path
        if (
            not return_to.startswith("/")
            or return_to.startswith("//")
            or "\\" in return_to
            or any(ord(c) <= 32 or ord(c) == 127 for c in return_to)
        ):
            raise HTTPException(400, "Invalid return_to.")
        parsed = urlsplit(return_to)
        if (
            parsed.scheme
            or parsed.netloc
            or parsed.fragment
            or "%" in parsed.path
            or any(part in {".", ".."} for part in parsed.path.split("/"))
            or not any(
                parsed.path == root
                or (root != "/" and parsed.path.startswith(root + "/"))
                for root in config.allowed_return_roots
            )
        ):
            raise HTTPException(400, "Invalid return_to.")
        return config.frontend_origin + return_to

    async def _sdk(self, method, *args):
        try:
            async with asyncio.timeout(self.settings.call_timeout_seconds):
                return await method(*args)
        except HTTPException:
            raise
        except Exception as exc:
            attribute = getattr(exc, "name", None)
            failed_object = getattr(exc, "obj", None)
            object_type = (
                type(failed_object).__name__
                if isinstance(exc, AttributeError)
                else None
            )
            log.warning(
                "sso_sdk_failed error_type=%s attribute=%s object_type=%s",
                type(exc).__name__,
                _diagnostic_identifier(attribute),
                _diagnostic_identifier(object_type),
            )
            raise HTTPException(503, "Corporate SSO is unavailable.") from None

    def _ttl(self, employee: VerifiedEmployee) -> int:
        ttl = self.settings.session_ttl_seconds
        if employee.valid_until_epoch is not None:
            if type(employee.valid_until_epoch) is not int:
                raise HTTPException(502, "Invalid SSO employee response.")
            ttl = min(ttl, int(employee.valid_until_epoch - time.time()))
        if ttl <= 0:
            raise HTTPException(
                401, "Corporate SSO authentication has expired."
            )
        return ttl

    async def login(
        self,
        request: Request,
        return_to: str = "/",
        target: Literal["app", "docs"] = "app",
    ) -> Response:
        destination = self._destination(return_to, target)
        employee = await self._sdk(self.adapter.verify, request)
        if employee is None:
            if not self.settings.allowed_origins:
                raise HTTPException(
                    503, "SSO login origins are not configured."
                )
            api_origin = self.settings.public_api_origin
            if api_origin is None:
                raise HTTPException(
                    503, "SSO public API origin is not configured."
                )
            callback = (
                api_origin
                + self.api_prefix
                + "/auth/login/sso?"
                + urlencode({"return_to": return_to, "target": target})
            )
            url = await self._sdk(self.adapter.login_url, request, callback)
            try:
                parsed = urlsplit(url)
                base = f"{parsed.scheme}://{parsed.netloc}"
                origin(base)
                if (
                    base not in self.settings.allowed_origins
                    or "\\" in url
                    or any(ord(c) <= 32 or ord(c) == 127 for c in url)
                ):
                    raise ValueError()
            except (ValueError, TypeError, AttributeError):
                raise HTTPException(
                    502, "Corporate SSO returned an invalid login URL."
                ) from None
            return RedirectResponse(
                url, status_code=302, headers={"Cache-Control": "no-store"}
            )
        if (
            not isinstance(employee, VerifiedEmployee)
            or not isinstance(employee.employee_id, str)
            or not employee.employee_id
            or not isinstance(employee.display_name, str)
        ):
            raise HTTPException(502, "Invalid SSO employee response.")
        self._ttl(employee)  # Do not provision an already expired employee.
        user_id = await self.users.bind(
            employee
        )  # Short DB unit of work, closed before Redis.
        ttl = self._ttl(employee)
        sid, _ = await self.sessions.create(user_id, ttl)
        await self.sessions.revoke(
            request.cookies.get(self.settings.cookie_name, "")
        )
        response = RedirectResponse(
            destination, status_code=302, headers={"Cache-Control": "no-store"}
        )
        response.set_cookie(
            self.settings.cookie_name,
            sid,
            max_age=ttl,
            path="/",
            secure=self.settings.cookie_secure,
            httponly=True,
            samesite=self.settings.cookie_samesite,
        )
        return response

    async def authenticate(
        self, request: Request, csrf_token: str | None
    ) -> LoginSession:
        session = await self.sessions.read(
            request.cookies.get(self.settings.cookie_name, "")
        )
        if session is None:
            raise HTTPException(401, "A valid login session is required.")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and (
            not csrf_token
            or not secrets.compare_digest(
                csrf_token.encode("utf-8"), session.csrf_token.encode("ascii")
            )
        ):
            raise HTTPException(403, "A valid X-CSRF-Token is required.")
        return session

    async def logout(
        self,
        request: Request,
        session: LoginDependency,
    ) -> Response:
        await self.sessions.revoke(
            request.cookies.get(self.settings.cookie_name, "")
        )
        response = Response(
            status_code=204, headers={"Cache-Control": "no-store"}
        )
        response.delete_cookie(
            self.settings.cookie_name,
            path="/",
            secure=self.settings.cookie_secure,
            httponly=True,
            samesite=self.settings.cookie_samesite,
        )
        return response

    def router(self):
        router = APIRouter(prefix=self.api_prefix + "/auth", tags=["auth"])
        router.add_api_route(
            "/login/sso",
            self.login,
            methods=["GET"],
            status_code=302,
            description=(
                "Start SSO using browser navigation or the Swagger SSO "
                "login link; Try it out is not the login "
                "UI."
            ),
        )
        router.add_api_route(
            "/logout", self.logout, methods=["POST"], status_code=204
        )
        return router

    async def close(self):
        if self._owned_redis is not None:
            await self._owned_redis.aclose()


def attach_sso(
    app,
    *,
    settings: SsoSettings,
    users: UserDirectory,
    redis_url: str,
    api_prefix: str,
    docs_path: str,
    adapter=None,
    redis=None,
) -> SsoRuntime:
    """Attach to an existing app. The caller owns lifetime and
    protects business routes explicitly.
    """
    if getattr(app.state, "sso", None) is not None:
        raise RuntimeError("SSO is already attached")
    adapter = adapter if adapter is not None else load_adapter(settings)
    owned = None
    if redis is None:
        # Short bursts wait for one of the bounded login connections rather
        # than immediately failing normal authenticated requests with 503.
        # The pool wait and socket I/O each retain an explicit finite deadline.
        pool = BlockingConnectionPool.from_url(
            redis_url,
            decode_responses=True,
            max_connections=settings.redis_max_connections,
            timeout=settings.redis_timeout_seconds,
            socket_connect_timeout=settings.redis_timeout_seconds,
            socket_timeout=settings.redis_timeout_seconds,
        )
        owned = redis = Redis.from_pool(
            pool
        )  # Own/close the pool with the runtime.
    runtime = SsoRuntime(
        settings,
        adapter,
        users,
        RedisSessions(redis, settings.namespace),
        api_prefix=api_prefix,
        docs_path=docs_path,
        owned_redis=owned,
    )
    app.state.sso = runtime
    app.include_router(runtime.router())
    return runtime
