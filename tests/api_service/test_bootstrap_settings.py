"""Configuration and lifecycle contract tests; no external service is contacted."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import AsyncMock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

import dtest.settings.loader as config_module
from dtest.settings.loader import (
    ConfigurationError,
    configure,
    get_settings,
    load_settings,
)
from dtest.bootstrap import BackgroundRuntime, attach_service, create_app

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def isolated_snapshot(monkeypatch):
    monkeypatch.setattr(config_module, "_snapshot", None)


def local_settings(**values):
    return load_settings(
        config={
            "AGENT_WORKER_ENABLED": False,
            "TASK_RECONCILER_ENABLED": False,
            "SHUTDOWN_DRAIN_SECONDS": 0,
            **values,
        },
        environ={},
    )


def test_config_wins_per_field_and_preserves_false_zero(monkeypatch):
    monkeypatch.setenv("MODEL_NAME", "must-not-leak")
    settings = load_settings(
        config={
            "MODEL_NAME": "yaml",
            "MODEL_MAX_RETRIES": 0,
            "EXECUTOR_SUBMIT_ENABLED": False,
        },
        environ={
            "LLM_MODEL_NAME": "environment",
            "MODEL_MAX_RETRIES": "8",
            "EXECUTOR_SUBMIT_ENABLED": "true",
            "MODEL_TIMEOUT_SECONDS": "42",
        },
    )
    assert settings.agent.model_name == "yaml"
    assert settings.agent.model_max_retries == 0
    assert settings.agent.executor_submit_enabled is False
    assert settings.agent.model_timeout_seconds == 42


def test_invalid_config_never_falls_back_and_hides_input_values():
    with pytest.raises(ConfigurationError) as exc:
        load_settings(
            config={"MODEL_TIMEOUT_SECONDS": "secret-invalid-value"},
            environ={"MODEL_TIMEOUT_SECONDS": "3"},
        )
    assert "secret-invalid-value" not in str(exc.value)


@pytest.mark.parametrize(
    "values",
    [
        {
            "CHECKPOINT_DB_URI": "postgresql://a/db",
            "AGENT_CHECKPOINT_DATABASE_URL": "postgresql://b/db",
        },
        {"MODEL_NAME": "one", "LLM_MODEL_NAME": "two"},
    ],
)
def test_alias_conflicts_are_rejected(values):
    with pytest.raises(ConfigurationError, match="Conflicting aliases"):
        load_settings(config={}, environ=values)


def test_config_checkpoint_override_applies_to_api_and_agent():
    settings = load_settings(
        config={
            "AGENT_CHECKPOINT_DATABASE_URL": "postgresql://config/checkpoints"
        },
        environ={"CHECKPOINT_DB_URI": "postgresql://env/checkpoints"},
    )
    assert (
        settings.agent.checkpoint_db_uri == "postgresql://config/checkpoints"
    )


def test_role_specific_databases_are_preserved():
    settings = local_settings(
        DATABASE_URL="postgresql+asyncpg://host/api",
        CHECKPOINT_DB_URI="postgresql://host/checkpoints",
        EW_DATABASE_URL="postgresql://host/events",
    )
    assert settings.database.database_url.endswith("/api")
    assert settings.agent.checkpoint_db_uri.endswith("/checkpoints")
    assert settings.worker.database_url.endswith("/events")


def test_missing_worker_database_derives_but_invalid_value_fails():
    settings = local_settings(
        DATABASE_URL="postgresql+asyncpg://u:p%40ss@host/api"
    )
    assert settings.worker.database_url == "postgresql://u:p%40ss@host/api"
    with pytest.raises(ConfigurationError, match="EW_DATABASE_URL"):
        local_settings(EW_DATABASE_URL="invalid-secret-url")


def test_agent_settings_have_one_owner():
    settings = local_settings(LLM_PROVIDER="mock")
    assert settings.agent.model_provider == "mock"
    assert (
        not {"llm_model_name", "checkpoint_db_uri", "executor_submit_enabled"}
        & type(settings.api).model_fields.keys()
    )
    assert not hasattr(settings, "workflow_database_url")


def test_statement_cache_is_opt_in_and_selected_profile_wins():
    assert (
        local_settings().database.database_prepared_statement_cache_size == 0
    )
    profile = load_settings(
        config_path=ROOT / "config.performance.yml",
        environ={"DATABASE_PREPARED_STATEMENT_CACHE_SIZE": "0"},
    )
    assert profile.database.database_prepared_statement_cache_size == 100
    assert profile.commands.agent_worker_concurrency == 32
    assert profile.database.database_pool_size == 10
    assert profile.database.database_max_overflow == 0
    assert profile.agent.checkpoint_pool_max_size == 4


@pytest.mark.parametrize("value", [-1, 1001, "invalid"])
def test_statement_cache_invalid_config_cannot_fall_back(value):
    with pytest.raises(ConfigurationError):
        load_settings(
            config={"DATABASE_PREPARED_STATEMENT_CACHE_SIZE": value},
            environ={"DATABASE_PREPARED_STATEMENT_CACHE_SIZE": "100"},
        )


def test_shared_yaml_reports_unused_keys_while_unrelated_environment_is_ignored():
    settings = load_settings(
        config={"MODLE_NAME": "typo-private-value"},
        environ={"UNRELATED_APP": "value"},
    )
    assert settings.unused_config_keys == ("MODLE_NAME",)
    assert "typo-private-value" not in str(settings.summary())
    assert "UNRELATED_APP" not in settings.unused_config_keys


def test_no_implicit_dotenv_or_phoenix_config(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("MODEL_NAME=leaked\n")
    (tmp_path / "config.dev.yml").write_text("PHOENIX_API_KEY: secret\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MODEL_NAME", "environment-leak")
    settings = load_settings(config={}, environ={})
    assert settings.agent.model_name not in {"leaked", "environment-leak"}
    from dtest.settings.agent import load_agent_settings

    assert (
        load_agent_settings({"MODEL_NAME": "explicit"}).phoenix_api_key is None
    )


def test_explicit_dotenv_does_not_mutate_environment(tmp_path, monkeypatch):
    path = tmp_path / "local.env"
    path.write_text("MODEL_NAME=dotenv\nMODEL_API_KEY=local-key\n")
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    settings = load_settings(
        config={"MODEL_NAME": "yaml"},
        environ={"MODEL_TIMEOUT_SECONDS": "12"},
        dotenv_path=path,
    )
    assert settings.agent.model_name == "yaml"
    assert settings.agent.model_api_key == "local-key"
    assert settings.agent.model_timeout_seconds == 12
    assert "MODEL_API_KEY" not in os.environ


def test_profile_selection_never_merges_local_file(tmp_path):
    (tmp_path / "config.yml").write_text(
        "MODEL_NAME: common\nMODEL_MAX_RETRIES: 7\n"
    )
    (tmp_path / "config.dev.yml").write_text("MODEL_NAME: development\n")
    settings = load_settings(
        environ={"APP_ENV": "dev", "MODEL_NAME": "env"}, root=tmp_path
    )
    assert settings.agent.model_name == "development"
    assert settings.agent.model_max_retries == 0
    assert settings.sources["MODEL_MAX_RETRIES"] == "default"
    with pytest.raises(ConfigurationError, match="Missing selected"):
        load_settings(environ={"APP_ENV": "stg"}, root=tmp_path)


def test_explicit_missing_config_fails(tmp_path):
    with pytest.raises(ConfigurationError, match="Cannot read YAML"):
        load_settings(config_path=tmp_path / "missing.yml", environ={})


def test_production_requires_explicit_databases():
    with pytest.raises(ConfigurationError, match="Deployment requires"):
        load_settings(config={}, environ={}, profile="prd")


@pytest.mark.parametrize(
    "key,value",
    [
        ("EXECUTOR_TLS_VERIFY", "not-bool"),
        ("CHECKPOINT_POOL_MIN_SIZE", 8),
        ("DATABASE_POOL_SIZE", 0),
        ("DATABASE_MAX_OVERFLOW", -1),
        ("SSE_POLL_INTERVAL_SECONDS", 0),
        ("GRAPH_CHECKPOINTER", "unsupported"),
        ("MODEL_NAME", None),
        ("MODEL_MOCK_DELAY_MS", -1),
    ],
)
def test_invalid_values_fail_without_fallback(key, value):
    with pytest.raises(ConfigurationError):
        local_settings(**{key: value})


def test_snapshot_is_shared_and_frozen():
    settings = configure(local_settings())
    from dtest.settings.agent import load_agent_settings
    from dtest.settings.api import settings as api_settings

    assert get_settings() is settings
    assert load_agent_settings() is settings.agent
    assert settings.database.database_url.startswith("postgresql")
    with pytest.raises(ValidationError):
        settings.database.database_url = "new"
    with pytest.raises(RuntimeError, match="already initialized"):
        configure(local_settings())


def test_safe_summary_omits_values():
    settings = local_settings(
        MODEL_API_KEY="super-secret",
        DATABASE_URL="postgresql+asyncpg://user:password@host/db",
    )
    text = json.dumps(settings.summary())
    assert (
        "super-secret" not in text
        and "password" not in text
        and "postgresql" not in text
    )


def test_platform_and_router_lifespans_are_preserved():
    events = []

    @asynccontextmanager
    async def platform_lifespan(app):
        events.append("platform-start")
        yield {"platform_marker": "preserved"}
        events.append("platform-stop")

    @asynccontextmanager
    async def router_lifespan(app):
        events.append("router-start")
        yield
        events.append("router-stop")

    async def worker():
        events.append("worker-start")
        try:
            await asyncio.Event().wait()
        finally:
            events.append("worker-stop")

    async def close():
        events.append("resources-close")

    app, router = (
        FastAPI(lifespan=platform_lifespan),
        APIRouter(lifespan=router_lifespan),
    )
    attach_service(
        app,
        local_settings(),
        router=router,
        background_factories={"test-worker": worker},
        close_resources=close,
    )
    with TestClient(app):
        assert app.state.service_runtime.ready
        assert events == ["platform-start", "router-start", "worker-start"]
    assert events == [
        "platform-start",
        "router-start",
        "worker-start",
        "worker-stop",
        "resources-close",
        "router-stop",
        "platform-stop",
    ]


def test_double_registration_is_rejected():
    app, settings = FastAPI(), local_settings()
    attach_service(
        app,
        settings,
        router=APIRouter(),
        background_factories={},
        close_resources=AsyncMock(),
    )
    with pytest.raises(RuntimeError, match="already attached"):
        attach_service(
            app,
            settings,
            router=APIRouter(),
            background_factories={},
            close_resources=AsyncMock(),
        )


def test_startup_failure_closes_started_siblings_and_resources():
    closed = []

    async def sibling():
        try:
            await asyncio.Event().wait()
        finally:
            closed.append("sibling")

    async def failure():
        raise RuntimeError("expected startup failure")

    close = AsyncMock()
    app = FastAPI()
    attach_service(
        app,
        local_settings(),
        router=APIRouter(),
        background_factories={"sibling": sibling, "failure": failure},
        close_resources=close,
    )
    with pytest.raises(RuntimeError, match="exited during startup"):
        with TestClient(app):
            pass
    assert closed == ["sibling"]
    close.assert_awaited_once()


@pytest.mark.asyncio
async def test_later_background_failure_makes_runtime_unready():
    fail = asyncio.Event()

    async def worker():
        await fail.wait()
        raise ValueError("failure")

    runtime = BackgroundRuntime({"worker": worker}, 1)
    await runtime.start()
    assert runtime.ready
    fail.set()
    await asyncio.sleep(0)
    assert not runtime.ready
    await runtime.stop()


def test_actual_app_openapi_and_health_need_no_external_services(monkeypatch):
    import dtest.infrastructure.database.runtime as database

    assert database._engine is None
    monkeypatch.setattr(
        database,
        "create_async_engine",
        lambda *a, **kw: pytest.fail("Unexpected DB engine"),
    )
    app = create_app(local_settings())
    operations = app.openapi()["paths"]
    assert "/api/v1/projects" in operations
    assert not any(
        path.startswith(("/api/v1/jupyter-servers", "/api/v1/redis"))
        for path in operations
    )
    assert not any(
        "JupyterServer" in name or "RedisPing" in name
        for name in app.openapi()["components"]["schemas"]
    )
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        # Removed routes must not run auth, DB or an external probe, even for POST.
        for method, path in (
            ("GET", "/api/v1/jupyter-servers"),
            (
                "GET",
                "/api/v1/jupyter-servers/00000000-0000-0000-0000-000000000001",
            ),
            (
                "POST",
                "/api/v1/jupyter-servers/00000000-0000-0000-0000-000000000001/health",
            ),
            ("GET", "/api/v1/redis/ping"),
        ):
            assert client.request(method, path).status_code == 404
        assert client.get("/service/ready").json() == {"ready": True}
    assert database._engine is None


def test_root_entrypoint_with_src_already_on_pythonpath():
    project_root = ROOT
    result = subprocess.run(
        [
            sys.executable,
            str(project_root / "app.py"),
            "--config",
            str(ROOT / "config.diagnostic.yml"),
            "--check-config",
        ],
        cwd=project_root,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["profile"] == "local"


@pytest.mark.asyncio
async def test_shutdown_deadline_reports_worker_that_ignores_cancellation():
    release = asyncio.Event()

    async def slow_cleanup():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()

    runtime = BackgroundRuntime({"slow-cleanup": slow_cleanup}, 0.01)
    await runtime.start()
    try:
        with pytest.raises(
            RuntimeError, match="shutdown deadline exceeded: slow-cleanup"
        ):
            await runtime.stop()
        assert not runtime.ready
    finally:
        release.set()
        await asyncio.gather(*runtime.tasks.values())
        await runtime.stop()


@pytest.mark.asyncio
async def test_all_owned_resources_close_when_one_cleanup_fails(monkeypatch):
    from dtest.bootstrap import _close_resources
    import dtest.infrastructure.database.runtime as database
    from dtest.application.runs.runtime import runtime

    configure(load_settings(config={}, environ={}))
    shutdown = AsyncMock(side_effect=RuntimeError("cleanup failed"))
    close_database = AsyncMock()
    monkeypatch.setattr(runtime, "shutdown", shutdown)
    monkeypatch.setattr(database, "close_database", close_database)
    with pytest.raises(RuntimeError, match="cleanup failed"):
        await _close_resources()
    close_database.assert_awaited_once()


def test_local_migration_guard_checks_resolved_yaml_target():
    import runpy

    bootstrap = runpy.run_path(str(ROOT / "scripts/local/bootstrap.py"))
    settings = load_settings(
        config={"DATABASE_URL": "postgresql+asyncpg://remote/chat_app"},
        environ={"DATABASE_URL": "postgresql+asyncpg://postgres/chat_app"},
    )
    configure(settings)
    assert (
        bootstrap["resolved_targets"]()["DATABASE_URL"]
        == settings.database.database_url
    )
    with pytest.raises(
        RuntimeError, match="DATABASE_URL must point at the local Compose"
    ):
        bootstrap["validate_local_targets"]()


@pytest.mark.parametrize(
    "values",
    [
        {"EW_POLL_SECONDS": float("inf")},
        {"LLM_TEMPERATURE": float("nan")},
        {"MODEL_PROVIDER": ""},
    ],
)
def test_nonfinite_or_empty_explicit_values_are_rejected(values):
    with pytest.raises(ConfigurationError):
        local_settings(**values)
