import asyncio
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from typing import Literal, ParamSpec, TypeVar
from urllib.parse import urlsplit

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from redis.asyncio import BlockingConnectionPool, Redis

from sso.contracts import SsoAdapter, UserDirectory, VerifiedEmployee
from sso.dependencies import LoginDependency
from sso.sdk.adapter import load_adapter
from sso.settings import SsoSettings, origin
from sso.storage.backend import RedisBackend
from sso.storage.login_flows import RedisLoginFlows
from sso.storage.sessions import (
    LoginSession,
    RedisSessions,
)

log = logging.getLogger(__name__)
_P = ParamSpec("_P")
_T = TypeVar("_T")


class SessionRead(BaseModel):
    """Current service login only; roles and profiles belong to the host."""

    user_id: str
    csrf_token: str
    expires_at: int


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
        flows: RedisLoginFlows,
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
        self.flows = flows
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

    async def _sdk(
        self,
        method: Callable[_P, Awaitable[_T]],
        *args: _P.args,
        **kwargs: _P.kwargs,
    ) -> _T:
        try:
            async with asyncio.timeout(self.settings.call_timeout_seconds):
                return await method(*args, **kwargs)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 - sanitize arbitrary SDK errors
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

    def _checked_login_url(
        self, url: object, *, callback_url: str | None = None
    ) -> str:
        # The SDK can return the exact server-generated callback after its
        # own handler succeeds. This is not proof of employee identity.
        if (
            isinstance(url, str)
            and callback_url is not None
            and url == callback_url
        ):
            return callback_url
        reason = "not_string"
        if isinstance(url, str):
            if not url:
                reason = "empty"
            elif "\\" in url or any(
                ord(char) <= 32 or ord(char) == 127 for char in url
            ):
                reason = "unsafe_characters"
            else:
                try:
                    parsed = urlsplit(url)
                    base = f"{parsed.scheme}://{parsed.netloc}"
                    origin(base)
                except (ValueError, TypeError, AttributeError):
                    reason = "invalid_origin"
                else:
                    if base in self.settings.allowed_origins:
                        return url
                    reason = "origin_not_allowed"
        # Fixed reason codes and bounded type names only. The SDK URL may
        # contain credentials, cookies or a ticket even when malformed.
        log.warning(
            "sso_login_url_rejected reason=%s value_type=%s",
            reason,
            _diagnostic_identifier(type(url).__name__),
        )
        raise HTTPException(
            502, "Corporate SSO returned an invalid login URL."
        ) from None

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
        sso_callback: bool = False,
    ) -> Response:
        destination = self._destination(return_to, target)
        employee = await self._sdk(self.adapter.verify, request)
        if employee is None:
            # A callback marker only stops retries; it never proves identity.
            if sso_callback:
                log.warning(
                    "sso_callback_unverified cookie_header_present=%s",
                    bool(request.headers.get("cookie")),
                )
                raise HTTPException(
                    401,
                    "Corporate SSO callback could not verify the employee. "
                    "Check the SDK callback and corporate cookie settings.",
                    headers={"Cache-Control": "no-store"},
                )
            if not self.settings.allowed_origins:
                raise HTTPException(
                    503, "SSO login origins are not configured."
                )
            api_origin = self.settings.public_api_origin
            if api_origin is None:
                raise HTTPException(
                    503, "SSO public API origin is not configured."
                )
            flow_id = secrets.token_urlsafe(32)
            callback = (
                api_origin
                + self.api_prefix
                + "/auth/login/sso/callback/"
                + flow_id
            )
            url = self._checked_login_url(
                await self._sdk(self.adapter.login_url, request, callback),
                callback_url=callback,
            )
            await self.flows.create(
                flow_id,
                return_to,
                target,
                self.settings.login_flow_ttl_seconds,
            )
            log.info(
                "sso_login_redirect destination=%s "
                "callback=%s cookie_header_present=%s",
                "service_callback" if url == callback else "corporate_sso",
                sso_callback,
                bool(request.headers.get("cookie")),
            )
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
        if not isinstance(user_id, str) or not user_id:
            raise HTTPException(502, "Invalid service user binding response.")
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
        log.info(
            "sso_login_redirect destination=application "
            "callback=%s cookie_header_present=%s",
            sso_callback,
            bool(request.headers.get("cookie")),
        )
        return response

    async def callback(self, request: Request, flow_id: str) -> Response:
        flow = await self.flows.consume(flow_id)
        if flow is None:
            raise HTTPException(
                400,
                "SSO login flow is invalid, expired or already used. "
                "Start login again.",
                headers={"Cache-Control": "no-store"},
            )
        # Never trust return_to/target query overrides or the flow as identity.
        return await self.login(
            request,
            return_to=flow.return_to,
            target=flow.target,
            sso_callback=True,
        )

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

    async def session(
        self, response: Response, session: LoginDependency
    ) -> SessionRead:
        response.headers["Cache-Control"] = "no-store"
        return SessionRead(
            user_id=session.user_id,
            csrf_token=session.csrf_token,
            expires_at=session.expires_at,
        )

    def router(self) -> APIRouter:
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
            "/login/sso/callback/{flow_id}",
            self.callback,
            methods=["GET"],
            status_code=302,
            description=(
                "SSO return endpoint with single-use Redis context; the "
                "SDK receives a callback URL without query parameters."
            ),
        )
        router.add_api_route(
            "/session",
            self.session,
            methods=["GET"],
            response_model=SessionRead,
        )
        router.add_api_route(
            "/logout", self.logout, methods=["POST"], status_code=204
        )
        return router

    async def close(self) -> None:
        if self._owned_redis is not None:
            await self._owned_redis.aclose()


def attach_sso(
    app: FastAPI,
    *,
    settings: SsoSettings,
    users: UserDirectory,
    redis_url: str,
    api_prefix: str = "/api/v1",
    docs_path: str = "/docs",
    adapter: SsoAdapter | None = None,
    redis: RedisBackend | None = None,
) -> SsoRuntime:
    """Attach to an existing app. The caller owns lifetime and
    protects business routes explicitly.
    """
    if getattr(app.state, "sso", None) is not None:
        raise RuntimeError("SSO is already attached")
    for path in (api_prefix, docs_path):
        if (
            not path.startswith("/")
            or path.startswith("//")
            or "\\" in path
            or any(c in path for c in "%?#{}")
            or any(ord(c) <= 32 or ord(c) == 127 for c in path)
            or any(part in {".", ".."} for part in path.split("/"))
            or path.endswith("/")
        ):
            raise ValueError("SSO API prefix/docs path must be absolute paths")
    adapter = adapter if adapter is not None else load_adapter(settings)
    owned: Redis | None = None
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
        RedisLoginFlows(redis, settings.namespace),
        api_prefix=api_prefix,
        docs_path=docs_path,
        owned_redis=owned,
    )
    app.state.sso = runtime
    app.include_router(runtime.router())
    return runtime
