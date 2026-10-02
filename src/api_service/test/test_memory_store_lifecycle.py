"""Store resource ownership is per lifespan, never per model call."""
import asyncio
from contextlib import AsyncExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from langgraph.store.memory import InMemoryStore
from api_service.core.memory_store import MemoryStoreRuntime, MemoryStoreBusy

@pytest.mark.asyncio
async def test_concurrent_store_borrows_initialize_once_and_shutdown_drains(monkeypatch):
    runtime=MemoryStoreRuntime();closed=AsyncMock();count=0
    async def initialize():
        nonlocal count
        count+=1
        await asyncio.sleep(.01)
        runtime.store=InMemoryStore();runtime.stack=AsyncExitStack()
        runtime.stack.push_async_callback(closed)
    monkeypatch.setattr(runtime,'initialize',initialize)
    async with runtime.open_store() as first:
        async with runtime.open_store() as second:
            assert first is second and count==1 and runtime.active==2
        shutdown=asyncio.create_task(runtime.shutdown());await asyncio.sleep(.01)
        assert not shutdown.done();closed.assert_not_awaited()
    await shutdown
    closed.assert_awaited_once();assert runtime.active==0 and runtime.store is None
    with pytest.raises(MemoryStoreBusy):
        async with runtime.open_store():pass
    runtime.start()
    async with runtime.open_store():assert count==2
    await runtime.shutdown()

@pytest.mark.asyncio
async def test_store_shutdown_timeout_keeps_pool_until_borrower_releases(monkeypatch):
    import service_settings
    monkeypatch.setattr(service_settings,'get_settings',lambda:SimpleNamespace(shutdown_timeout_seconds=.01))
    runtime=MemoryStoreRuntime();runtime.store=InMemoryStore();runtime.stack=AsyncExitStack();closed=AsyncMock()
    runtime.stack.push_async_callback(closed)
    async with runtime.open_store():
        with pytest.raises(MemoryStoreBusy):await runtime.shutdown()
        closed.assert_not_awaited();assert runtime.store is not None
    await runtime.shutdown();closed.assert_awaited_once()
