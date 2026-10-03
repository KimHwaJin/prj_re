"""Actual pool concurrency and cancellation without a fragile wall-time target."""
import asyncio
from uuid import uuid4

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool
from agent_service.runtime.langgraph.checkpointer import create_checkpointer
from api_service.test.test_user_identity_postgres import database_url, harness
from api_service.test.test_async_llm_postgres import checkpoint_url


@pytest.mark.asyncio
async def test_operations_use_bounded_shared_pool_and_return_connections(harness, database_url, monkeypatch):
    entered=asyncio.Event(); release=asyncio.Event(); active=peak=0; connections=set()
    async def blocked_read(self, config):
        nonlocal active,peak
        async with self._cursor() as cur:
            await cur.execute('SELECT 1')
            assert (await cur.fetchone())['?column?']==1
            connections.add(cur.connection.info.backend_pid)
            active+=1; peak=max(peak,active)
            if active==2: entered.set()
            try: await release.wait()
            finally: active-=1
        return None
    monkeypatch.setattr(AsyncPostgresSaver,'aget_tuple',blocked_read)
    async with create_checkpointer(checkpoint_url(database_url),setup_on_start=True,min_size=2,max_size=2) as saver:
        assert isinstance(saver.conn,AsyncConnectionPool)
        tasks=[asyncio.create_task(saver.aget_tuple({'configurable':{'thread_id':str(uuid4())}})) for _ in range(4)]
        try:
            await asyncio.wait_for(entered.wait(),5)
            assert peak==2 and saver.conn.get_stats()['pool_size']==2
            assert len(connections)==2
        finally:
            release.set(); await asyncio.gather(*tasks)
        assert active==0 and peak==2 and saver.conn.get_stats()['pool_available']==2


@pytest.mark.asyncio
async def test_cancelled_operation_releases_its_pool_connection(harness,database_url,monkeypatch):
    entered=asyncio.Event()
    async def blocked_read(self, config):
        async with self._cursor() as cur:
            await cur.execute('SELECT 1')
            entered.set(); await asyncio.Event().wait()
    monkeypatch.setattr(AsyncPostgresSaver,'aget_tuple',blocked_read)
    async with create_checkpointer(checkpoint_url(database_url),setup_on_start=True,min_size=1,max_size=1) as saver:
        task=asyncio.create_task(saver.aget_tuple({'configurable':{'thread_id':str(uuid4())}}))
        await asyncio.wait_for(entered.wait(),5); task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert saver.conn.get_stats()['pool_available']==1
        async with saver.conn.connection() as conn:
            assert (await (await conn.execute('SELECT 1')).fetchone())['?column?']==1
