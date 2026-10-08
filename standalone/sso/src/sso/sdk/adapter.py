"""SDK interface; the corporate implementation stays in the closed network."""

import importlib
import inspect
from collections.abc import Callable

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool

from sso.contracts import SsoAdapter, VerifiedEmployee
from sso.sdk.request import sdk_request
from sso.settings import SsoSettings


class UnconfiguredAdapter:
    async def verify(self, request: Request) -> VerifiedEmployee | None:
        raise HTTPException(503, "Corporate SSO adapter is not configured.")

    async def login_url(self, request: Request, return_url: str) -> str:
        raise HTTPException(503, "Corporate SSO adapter is not configured.")


class SyncSsoAdapter:
    """Offload synchronous calls; also configure SDK network timeouts.

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
        return await run_in_threadpool(
            self._login_url, sdk_request(request, return_url), return_url
        )


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
    except Exception:  # noqa: BLE001 - sanitize private factory errors
        # Import/constructor errors can expose private config. Do not log them.
        raise ValueError(
            "Cannot load SSO_ADAPTER_FACTORY; check the private "
            "adapter contract."
        ) from None
