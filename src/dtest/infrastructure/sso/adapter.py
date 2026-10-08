"""SDK interface only. Corporate implementation is supplied inside the closed network."""

import importlib
import inspect
from collections.abc import Callable

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool

from dtest.contracts.auth import SsoAdapter, VerifiedEmployee
from dtest.infrastructure.sso.request import sdk_request
from dtest.settings.auth import SsoSettings


class UnconfiguredAdapter:
    async def verify(self, request: Request) -> VerifiedEmployee | None:
        raise HTTPException(503, "Corporate SSO adapter is not configured.")

    async def login_url(self, request: Request, return_url: str) -> str:
        raise HTTPException(503, "Corporate SSO adapter is not configured.")


class SyncSsoAdapter:
    """Offload synchronous SDK calls. Configure the SDK's own network timeout too.

    The SDK sees a string request.url; other request reads are delegated.
    This does not emulate Flask session globals or determine the protocol.
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
            self._login_url, sdk_request(request), return_url
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
    except Exception:
        # Import/constructor failures can expose corporate config; don't interpolate the exception.
        raise ValueError(
            "Cannot load SSO_ADAPTER_FACTORY; check the private "
            "adapter contract."
        ) from None
