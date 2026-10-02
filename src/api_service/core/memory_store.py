"""One lazy official AsyncPostgresStore/pool per API process and event loop.

Uses DATABASE_URL, not checkpoint or Executor-event DB settings. No startup DDL,
embedding model, vector index or TTL. The asyncpg CRUD pool cannot be handed to
psycopg; this bounded pool is included separately in the per-Pod DB budget.
"""
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from langgraph.store.postgres import AsyncPostgresStore
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from sqlalchemy.engine import make_url
from service_runtime.cleanup import protected_cleanup
from service_runtime.diagnostics import observe_pool

class MemoryStoreBusy(RuntimeError):
    pass

class MemoryStoreRuntime:
    def __init__(self):
        self.store = None
        self.stack = None
        self.active = 0
        self.closing = False
        self.loop = None
        self.lock = asyncio.Lock()
        self.drained = asyncio.Event()
        self.drained.set()

    def start(self):
        if self.stack is not None or self.active or self.lock.locked():
            raise MemoryStoreBusy('Memory Store still owns resources')
        self.closing = False
        self.loop = None
        self.lock = asyncio.Lock()
        self.drained = asyncio.Event()
        self.drained.set()

    async def initialize(self):
        from service_settings import get_settings
        api = get_settings().api
        uri = make_url(api.database_url).set(drivername='postgresql').render_as_string(hide_password=False)
        pool = AsyncConnectionPool(uri, name='project-memory-store', min_size=0,
            max_size=min(2, api.database_pool_size), timeout=api.database_pool_timeout_seconds,
            open=False, check=AsyncConnectionPool.check_connection,
            kwargs={'autocommit': True, 'row_factory': dict_row, 'connect_timeout': 10, 'prepare_threshold': 0})
        observe_pool(pool, 'memory_store_pool')
        stack = AsyncExitStack()
        stack.push_async_callback(pool.close)
        try:
            await pool.open(wait=True)
            store = AsyncPostgresStore(pool)
            await stack.enter_async_context(store)
        except BaseException:
            await stack.aclose()
            raise
        self.stack, self.store = stack, store

    @asynccontextmanager
    async def open_store(self):
        loop = asyncio.get_running_loop()
        if self.loop is not None and self.loop is not loop:
            raise MemoryStoreBusy('Memory Store cannot cross event loops')
        self.loop = loop
        async with self.lock:
            if self.closing:
                raise MemoryStoreBusy('Memory Store is shutting down')
            if self.store is None:
                await protected_cleanup(self.initialize())
            self.active += 1
            self.drained.clear()
        try:
            yield self.store
        finally:
            self.active -= 1
            if not self.active:
                self.drained.set()

    async def shutdown(self):
        from service_settings import get_settings
        self.closing = True
        try:
            async with asyncio.timeout(get_settings().shutdown_timeout_seconds):
                async with self.lock:
                    await self.drained.wait()
                    if self.stack is not None:
                        await protected_cleanup(self.stack.aclose())
                    self.stack = self.store = self.loop = None
        except TimeoutError as exc:
            raise MemoryStoreBusy('Memory Store has active borrowers') from exc

runtime = MemoryStoreRuntime()
