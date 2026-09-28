"""API-side access to Worker bindings without starting consumers."""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Self

from psycopg_pool import AsyncConnectionPool
from redis.asyncio import Redis

from app.worker import Settings
from app.worker.guard import SessionGuard
from app.worker.store import Store
from app.core.run_diagnostics import observe_pool


_api_worker_bridge: "ApiWorkerBridge | None" = None


class ApiWorkerBridge:
    """Open the binding store and session guard in the API process."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.pool = AsyncConnectionPool(
            settings.database_url,
            min_size=1,
            max_size=settings.pool_size,
            open=False,
        )
        observe_pool(self.pool, "bridge_pool")
        self.redis = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=settings.request_timeout_seconds,
            socket_timeout=max(10, settings.request_timeout_seconds),
        )
        self.store = Store(self.pool, settings.namespace)
        self.bindings = self.store
        self.guard = SessionGuard(
            self.redis,
            settings.namespace,
            ttl=settings.lease_ttl_seconds,
            renew_seconds=settings.lease_renew_seconds,
        )
        self._stack = AsyncExitStack()

    async def __aenter__(self) -> Self:
        try:
            self._stack.push_async_callback(self.redis.aclose)
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
