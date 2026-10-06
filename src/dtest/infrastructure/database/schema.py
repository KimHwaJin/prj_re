"""One ordered schema preparation path for startup and the migration CLI."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from threading import Lock

import psycopg
from alembic import command
from alembic.config import Config
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy.engine import make_url

from dtest.lifecycle import protected_cleanup
from dtest.settings.models import ServiceSettings

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[4]
# Session lock spans both Alembic chains and SDK setup, including commits.
# It is scoped to the primary DB shared by this service's replicas.
SCHEMA_LOCK_KEY = 178521094
LOCK_TIMEOUT_MS = 60_000
# Alembic's context proxy is process-global; serialize threads as well as Pods.
_initialization_lock = Lock()


async def _prepare_checkpoint(settings: ServiceSettings) -> None:
    async with AsyncPostgresSaver.from_conn_string(
        settings.agent.checkpoint_db_uri
    ) as saver:
        await saver.setup()


def initialize_databases(
    settings: ServiceSettings, *, configure_logging: bool = True
) -> None:
    """Upgrade existing DBs without creating DBs or resetting data."""
    configs = []
    for name in ("alembic.crud.ini", "alembic.ini"):
        ini = ROOT / name
        if not ini.is_file():
            raise RuntimeError("Database migration resources are missing")
        config = Config(str(ini))
        path = config.get_main_option("script_location")
        if path is None or not Path(path).is_dir():
            raise RuntimeError("Database migration resources are missing")
        config.attributes["configure_logger"] = configure_logging
        configs.append(config)

    uri = (
        make_url(settings.database.database_url)
        .set(drivername="postgresql")
        .render_as_string(hide_password=False)
    )
    stage = "lock"
    try:
        with (
            _initialization_lock,
            psycopg.connect(
                uri, autocommit=True, connect_timeout=10
            ) as connection,
        ):
            # Only this coordination connection receives the bounded timeout.
            connection.execute(
                "SELECT set_config('statement_timeout', %s, false)",
                (str(LOCK_TIMEOUT_MS),),
            )
            connection.execute(
                "SELECT pg_advisory_lock(%s)", (SCHEMA_LOCK_KEY,)
            )
            try:
                log.info("schema_initialization_started")
                for stage, config in zip(("crud", "event"), configs):
                    log.info("schema_initialization_stage stage=%s", stage)
                    command.upgrade(config, "head")
                stage = "checkpoint"
                log.info("schema_initialization_stage stage=%s", stage)
                with asyncio.Runner(
                    loop_factory=asyncio.SelectorEventLoop
                    if sys.platform == "win32"
                    else None
                ) as runner:
                    runner.run(_prepare_checkpoint(settings))
            finally:
                # Closing the dedicated connection also releases its lock if
                # unlock fails or the process terminates during migration.
                connection.execute(
                    "SELECT pg_advisory_unlock(%s)", (SCHEMA_LOCK_KEY,)
                )
        log.info("schema_initialization_completed")
    except Exception as error:
        # DB exceptions can include SQL payloads/credentials; log types only.
        log.error(
            "schema_initialization_failed stage=%s error_type=%s",
            stage,
            type(error).__name__,
        )
        raise


async def initialize_schema(settings: ServiceSettings) -> None:
    """Keep the lifespan loop responsive and own the migration thread."""
    try:
        await protected_cleanup(
            asyncio.to_thread(
                initialize_databases, settings, configure_logging=False
            )
        )
    except Exception as error:  # noqa: BLE001 - redact startup DB exceptions
        # Manual migrate.py retains the original traceback for diagnosis.
        raise RuntimeError(
            "Database schema initialization failed "
            f"({type(error).__name__}); API/Workers were not started. "
            "Check the selected DB settings and run scripts/migrate.py "
            "with the same configuration for details."
        ) from None
