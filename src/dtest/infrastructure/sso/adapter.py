"""SDK interface only. Corporate implementation is supplied inside the closed network."""

import importlib
import inspect
import logging
from collections.abc import Callable
from urllib.parse import parse_qs, urlsplit

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool

from dtest.contracts.auth import SsoAdapter, VerifiedEmployee
from dtest.infrastructure.sso.request import sdk_request
from dtest.settings.auth import SsoSettings

log = logging.getLogger(__name__)


def _callback_binding(login_url: object, return_url: str) -> str:
    """Diagnose the observed SDK's redirect_uri without exposing any URL.

    This is diagnostic only: SDKs using other fields report missing, and
    different does not imply an invalid URL or failed authentication.
    """
    if not isinstance(login_url, str):
        return "unreadable"
    try:
        values = parse_qs(
            urlsplit(login_url).query, keep_blank_values=True
        ).get("redirect_uri", [])
    except ValueError:
        return "unreadable"
    if not values:
        return "missing"
    if len(values) != 1:
        return "multiple"
    return "matches" if values[0] == return_url else "differs"


class UnconfiguredAdapter:
    async def verify(self, request: Request) -> VerifiedEmployee | None:
        raise HTTPException(503, "Corporate SSO adapter is not configured.")

    async def login_url(self, request: Request, return_url: str) -> str:
        raise HTTPException(503, "Corporate SSO adapter is not configured.")


class SyncSsoAdapter:
    """Offload synchronous SDK calls. Configure the SDK's own network timeout too.

    The SDK sees string URL and Flask-style args/cookies/environ metadata.
    Login URL creation binds the server return_url to SDK args ORIGIN.
    Verification keeps the original args. No Flask session globals.
    """

    def __init__(
        self,
        verify: Callable[[Request], VerifiedEmployee | None],
        login_url: Callable[[Request, str], str],
    ):
        self._verify = verify
        self._login_url = login_url

    async def verify(self, request: Request) -> VerifiedEmployee | None:
        return await run_in_threadpool(self._verify, sdk_request(request))

    async def login_url(self, request: Request, return_url: str) -> str:
        url = await run_in_threadpool(
            self._login_url, sdk_request(request, return_url), return_url
        )
        log.info(
            "sso_sdk_callback_binding redirect_uri=%s",
            _callback_binding(url, return_url),
        )
        return url


def load_adapter(settings: SsoSettings) -> SsoAdapter:
    if not settings.adapter_factory:
        return UnconfiguredAdapter()
    module, name = settings.adapter_factory.split(":", 1)
    try:
        adapter = getattr(importlib.import_module(module), name)(settings)
        if not all(
            inspect.iscoroutinefunction(getattr(adapter, method, None))
            for method in ("verify", "login_url")
        ):
            raise TypeError()
        return adapter
    except Exception:
        # Import/constructor failures can expose corporate config; don't interpolate the exception.
        raise ValueError(
            "Cannot load SSO_ADAPTER_FACTORY; check the private "
            "adapter contract."
        ) from None
