from __future__ import annotations

import asyncio
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, make_url, pool, text
from sqlalchemy.ext.asyncio import create_async_engine
from dtest.settings.loader import get_settings

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Resolve the same target as running consumers. Loading settings performs no IO
# to DB/Redis/LLM and does not start a Worker.
raw_url = get_settings().worker.database_url
url = make_url(raw_url)
if url.drivername not in {"postgres", "postgresql", "postgresql+psycopg"}:
    raise ValueError("Worker migrations require PostgreSQL with psycopg")
url = url.set(drivername="postgresql+psycopg")

VERSION_TABLE = "ew_alembic_version"


def run_offline() -> None:
    context.configure(
        url=url,
        target_metadata=None,
        version_table=VERSION_TABLE,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_on_connection(connection: Connection) -> None:
    # Same transaction lock as the legacy Store.migrate(), acquired before
    # Alembic inspects/creates its version table. DDL and revision are atomic.
    connection.execute(text("SELECT pg_advisory_xact_lock(178521093)"))
    context.configure(
        connection=connection,
        target_metadata=None,
        version_table=VERSION_TABLE,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_online() -> None:
    engine = create_async_engine(url, poolclass=pool.NullPool)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(run_on_connection)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_offline()
else:
    # Psycopg async requires Selector on Windows, including Alembic's loops.
    with asyncio.Runner(
        loop_factory=asyncio.SelectorEventLoop
        if sys.platform == "win32"
        else None
    ) as runner:
        runner.run(run_online())
