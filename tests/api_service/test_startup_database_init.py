"""Startup initialization ordering, switch precedence and failure handling."""

import asyncio
import threading
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from dtest.bootstrap import attach_service
from dtest.infrastructure.database import schema
from dtest.settings import loader


@pytest.fixture(autouse=True)
def snapshot(monkeypatch):
    monkeypatch.setattr(loader, "_snapshot", None)


def settings(**values):
    return loader.load_settings(
        config={
            "AGENT_WORKER_ENABLED": False,
            "EVENT_WORKER_ENABLED": False,
            "TASK_RECONCILER_ENABLED": False,
            "SHUTDOWN_DRAIN_SECONDS": 0,
            **values,
        },
        environ={},
    )


def test_switch_default_and_environment_yaml_precedence():
    assert not loader.load_settings(config={}, environ={}).db_init_on_start
    enabled = loader.load_settings(
        config={}, environ={"DB_INIT_ON_START": "true"}
    )
    assert enabled.db_init_on_start
    assert enabled.summary()["db_init_on_start"]
    assert enabled.sources["DB_INIT_ON_START"] == "environment"
    disabled = loader.load_settings(
        config={"DB_INIT_ON_START": False},
        environ={"DB_INIT_ON_START": "true"},
    )
    assert not disabled.db_init_on_start
    assert disabled.sources["DB_INIT_ON_START"] == "config mapping"
    with pytest.raises(loader.ConfigurationError):
        loader.load_settings(
            config={}, environ={"DB_INIT_ON_START": "invalid"}
        )


@pytest.mark.parametrize("enabled", [False, True])
def test_init_precedes_platform_and_worker_start(monkeypatch, enabled):
    events = []
    config = settings(DB_INIT_ON_START=enabled)

    async def initialize(selected):
        assert selected is config
        events.append("init")

    @asynccontextmanager
    async def lifespan(app):
        events.append("platform")
        yield

    async def worker():
        events.append("worker")
        await asyncio.Event().wait()

    monkeypatch.setattr(schema, "initialize_schema", initialize)
    app = FastAPI(lifespan=lifespan)
    attach_service(
        app,
        config,
        router=APIRouter(),
        background_factories={"worker": worker},
        close_resources=AsyncMock(),
    )
    with TestClient(app):
        assert app.state.service_runtime.ready
        assert events == (["init"] if enabled else []) + ["platform", "worker"]


def test_init_failure_does_not_start_workers_and_closes_sso(monkeypatch):
    initialize = AsyncMock(side_effect=RuntimeError("migration failed"))
    monkeypatch.setattr(schema, "initialize_schema", initialize)
    worker = AsyncMock()
    app = FastAPI()
    attach_service(
        app,
        settings(DB_INIT_ON_START=True),
        router=APIRouter(),
        background_factories={"worker": worker},
        close_resources=AsyncMock(),
    )
    close = AsyncMock()
    monkeypatch.setattr(app.state.sso, "close", close)
    with (
        pytest.raises(RuntimeError, match="migration failed"),
        TestClient(app),
    ):
        pytest.fail("must not serve requests before initialization")
    worker.assert_not_called()
    close.assert_awaited_once()
    assert not app.state.service_runtime.started


@pytest.mark.asyncio
async def test_thread_keeps_loop_responsive_and_cancellation_owns_it(
    monkeypatch,
):
    started, release, finished = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )

    def initialize(config, *, configure_logging):
        assert not configure_logging
        started.set()
        assert release.wait(5)
        finished.set()

    monkeypatch.setattr(schema, "initialize_databases", initialize)
    task = asyncio.create_task(schema.initialize_schema(settings()))
    try:
        async with asyncio.timeout(5):
            while not started.is_set():
                await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done()
        assert not finished.is_set()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()


def test_schema_failure_releases_lock_and_preserves_cause_for_cli(monkeypatch):
    connection = MagicMock()
    connection.__enter__.return_value = connection
    monkeypatch.setattr(schema.psycopg, "connect", lambda *a, **k: connection)
    monkeypatch.setattr(
        schema.command, "upgrade", MagicMock(side_effect=RuntimeError("DDL"))
    )
    with pytest.raises(RuntimeError, match="DDL"):
        schema.initialize_databases(settings(), configure_logging=False)
    queries = [call.args[0] for call in connection.execute.call_args_list]
    assert queries[-1] == "SELECT pg_advisory_unlock(%s)"
    connection.__exit__.assert_called_once()
    assert not schema._initialization_lock.locked()


@pytest.mark.asyncio
async def test_startup_error_redacts_private_exception_and_aborts(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("private DB credential/payload")

    monkeypatch.setattr(schema, "initialize_databases", fail)
    with pytest.raises(
        RuntimeError, match="API/Workers were not started"
    ) as exc:
        await schema.initialize_schema(settings())
    assert "private DB credential/payload" not in str(exc.value)
    assert exc.value.__suppress_context__


def test_missing_migration_resources_fail_before_db_connection(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(schema, "ROOT", tmp_path)
    connect = MagicMock()
    monkeypatch.setattr(schema.psycopg, "connect", connect)
    with pytest.raises(RuntimeError, match="resources are missing"):
        schema.initialize_databases(settings())
    connect.assert_not_called()
