"""API-side access to Worker bindings without starting consumers."""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Self

from psycopg_pool import AsyncConnectionPool

from dtest.infrastructure.database.event_store import Store
from dtest.infrastructure.observability.diagnostics import observe_pool
from dtest.settings.events import EventWorkerSettings


class ApiWorkerBridge:
    """Open the binding store; graph ownership lives in the shared API DB."""

    def __init__(self, settings: EventWorkerSettings) -> None:
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
