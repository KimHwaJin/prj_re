"""LangGraph API lifecycle for the shared PostgreSQL checkpointer."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from dtest.infrastructure.observability.diagnostics import (
    instrument_async_methods,
    observe_pool,
)
from dtest.settings.agent import load_agent_settings

from .pooled_saver import PooledAsyncPostgresSaver

logger = logging.getLogger(__name__)


@asynccontextmanager
async def create_checkpointer(
    database_url: str | None = None,
    *,
    setup_on_start: bool | None = None,
    min_size: int | None = None,
    max_size: int | None = None,
    timeout: float | None = None,
) -> AsyncIterator[AsyncPostgresSaver]:
    settings = load_agent_settings()
    database_url = (
        database_url
        if database_url is not None
        else settings.checkpoint_db_uri
    ).strip()
    if not database_url:
        raise RuntimeError("CHECKPOINT_DB_URI is required")

    min_size = (
        settings.checkpoint_pool_min_size if min_size is None else min_size
    )
    max_size = (
        settings.checkpoint_pool_max_size if max_size is None else max_size
    )
    timeout = (
        settings.checkpoint_pool_timeout_seconds
        if timeout is None
        else timeout
    )
    if not 1 <= min_size <= max_size or not 0 < timeout < float("inf"):
        raise ValueError("Invalid checkpoint pool size or timeout")
    if setup_on_start is None:
        setup_on_start = settings.checkpoint_setup_on_start
    pool = AsyncConnectionPool(
        conninfo=database_url,
        name="agent-checkpoint",
        min_size=min_size,
        max_size=max_size,
        timeout=timeout,
        open=False,
        check=AsyncConnectionPool.check_connection,
        kwargs={
            "autocommit": True,
            "row_factory": dict_row,
            "connect_timeout": 10,
        },
    )
    observe_pool(pool, "checkpoint_pool")
    try:
        await pool.open(wait=True, timeout=timeout)
        logger.info("Checkpoint pool ready: %s", pool.get_stats())
        checkpointer = PooledAsyncPostgresSaver(pool)
        instrument_async_methods(
            checkpointer, "checkpoint", ("aget_tuple", "aput", "aput_writes")
        )
        if setup_on_start:
            await checkpointer.setup()
        yield checkpointer
    finally:
        logger.info("Checkpoint pool closing: %s", pool.get_stats())
        await pool.close()


__all__ = ["create_checkpointer"]
