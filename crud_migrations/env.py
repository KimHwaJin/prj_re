from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from dtest.settings.loader import get_settings
from dtest.infrastructure.database.models.model_base import Base

# Register every mapped table on Base.metadata before autogenerate runs.
import dtest.infrastructure.database.models  # noqa: F401,E402


config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database.database_url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def include_object(obj, name, type_, reflected, compare_to):
    # Active SDK/other-chain tables share the DB but are not ORM-owned.
    # Retired tables have no exemption. Explicit retirement DDL is unaffected.
    if type_ == "index" and name.startswith("ix_workflow_hnsw_"):
        return False  # model-space indexes are managed by the provisioning tool
    active_external_tables = {
        'store', 'store_migrations',
        'checkpoints', 'checkpoint_blobs', 'checkpoint_writes', 'checkpoint_migrations',
        'ew_bindings', 'ew_inbox', 'ew_alembic_version',
    }
    return not (type_ == 'table' and name in active_external_tables
                and reflected and compare_to is None)


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
