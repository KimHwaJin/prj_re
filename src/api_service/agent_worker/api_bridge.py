"""API-side access to Worker bindings without starting consumers."""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Self

from psycopg_pool import AsyncConnectionPool

from api_service.worker import Settings
from api_service.worker.store import Store
from service_runtime.diagnostics import observe_pool


_api_worker_bridge: "ApiWorkerBridge | None" = None


class ApiWorkerBridge:
    """Open the binding store; graph ownership lives in the shared API DB."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.pool = AsyncConnectionPool(
            settings.database_url,
            min_size=1,
            max_size=settings.pool_size,
            open=False,
        )
        observe_pool(self.pool, "bridge_pool")
        self.store = Store(self.pool, settings.namespace)
        self.bindings = self.store
        self._stack = AsyncExitStack()

    async def __aenter__(self) -> Self:
        try:
            self._stack.push_async_callback(self.pool.close)
            await self.pool.open(wait=True)
        except BaseException:
            await self._stack.aclose()
            raise
        return self

    async def __aexit__(self, *args: object) -> None:
        await self._stack.aclose()


def get_api_worker_bridge() -> ApiWorkerBridge:
    """Return the process-wide bridge shared by the graph and API lifespan."""
    global _api_worker_bridge
    if _api_worker_bridge is None:
        from service_settings import get_settings
        _api_worker_bridge = ApiWorkerBridge(get_settings().worker)
    return _api_worker_bridge


async def close_api_worker_bridge() -> None:
    global _api_worker_bridge
    bridge, _api_worker_bridge = _api_worker_bridge, None
    if bridge is not None:
        await bridge.__aexit__(None, None, None)


__all__ = ["ApiWorkerBridge", "get_api_worker_bridge"]
