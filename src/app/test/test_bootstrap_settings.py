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

import service_settings as config_module
from service_settings import ConfigurationError, configure, get_settings, load_settings
from service_bootstrap import BackgroundRuntime, attach_service, create_app

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def isolated_snapshot(monkeypatch):
    monkeypatch.setattr(config_module, "_snapshot", None)


def local_settings(**values):
    return load_settings(config={"AGENT_WORKER_ENABLED": False, "TASK_RECONCILER_ENABLED": False, **values}, environ={})


def test_config_wins_per_field_and_preserves_false_zero(monkeypatch):
    monkeypatch.setenv("MODEL_NAME", "must-not-leak")
    settings = load_settings(
        config={"service": {"llm": {"model_name": "yaml", "model_max_retries": 0},
                            "executor": {"executor_submit_enabled": False}}},
        environ={"LLM_MODEL_NAME": "environment", "MODEL_MAX_RETRIES": "8",
                 "EXECUTOR_SUBMIT_ENABLED": "true", "MODEL_TIMEOUT_SECONDS": "42"},
    )
    assert settings.api.llm_model_name == settings.agent.model_name == "yaml"
    assert settings.api.llm_max_retries == settings.agent.model_max_retries == 0
    assert settings.api.executor_submit_enabled is settings.agent.executor_submit_enabled is False
    assert settings.agent.model_timeout_seconds == settings.api.llm_timeout_seconds == 42


def test_invalid_config_never_falls_back_and_hides_input_values():
    with pytest.raises(ConfigurationError) as exc:
        load_settings(config={"MODEL_TIMEOUT_SECONDS": "secret-invalid-value"},
                      environ={"MODEL_TIMEOUT_SECONDS": "3"})
    assert "secret-invalid-value" not in str(exc.value)


@pytest.mark.parametrize("values", [
    {"CHECKPOINT_DB_URI": "postgresql://a/db", "AGENT_CHECKPOINT_DATABASE_URL": "postgresql://b/db"},
    {"MODEL_NAME": "one", "LLM_MODEL_NAME": "two"},
])
def test_alias_conflicts_are_rejected(values):
    with pytest.raises(ConfigurationError, match="Conflicting aliases"):
        load_settings(config={}, environ=values)


def test_config_checkpoint_override_applies_to_api_and_agent():
    settings = load_settings(config={"AGENT_CHECKPOINT_DATABASE_URL": "postgresql://config/checkpoints"},
                             environ={"CHECKPOINT_DB_URI": "postgresql://env/checkpoints"})
    assert settings.api.checkpoint_db_uri == settings.agent.checkpoint_db_uri == "postgresql://config/checkpoints"


def test_role_specific_databases_are_preserved():
    settings = local_settings(DATABASE_URL="postgresql+asyncpg://host/api",
                              CHECKPOINT_DB_URI="postgresql://host/checkpoints",
                              EW_DATABASE_URL="postgresql://host/events",
                              WORKFLOW_DATABASE_URL="postgresql://host/catalog")
    assert settings.api.database_url.endswith("/api")
    assert settings.agent.checkpoint_db_uri.endswith("/checkpoints")
    assert settings.worker.database_url.endswith("/events")
    assert settings.workflow_database_url.endswith("/catalog")


def test_missing_worker_database_derives_but_invalid_value_fails():
    settings = local_settings(DATABASE_URL="postgresql+asyncpg://u:p%40ss@host/api")
    assert settings.worker.database_url == "postgresql://u:p%40ss@host/api"
    with pytest.raises(ConfigurationError, match="EW_DATABASE_URL"):
        local_settings(EW_DATABASE_URL="invalid-secret-url")


def test_defaults_do_not_diverge():
    settings = local_settings()
    assert settings.api.checkpoint_db_uri == settings.agent.checkpoint_db_uri
    assert settings.api.executor_shared_input_root == settings.agent.executor_shared_input_root
    assert settings.api.executor_submit_enabled == settings.agent.executor_submit_enabled
    assert settings.api.llm_temperature == settings.agent.model_temperature


def test_unknown_yaml_key_fails_while_unrelated_environment_is_ignored():
    with pytest.raises(ConfigurationError, match="Unknown service setting"):
        load_settings(config={"service": {"llm": {"modle_name": "typo"}}}, environ={})
    assert load_settings(config={}, environ={"UNRELATED_APP": "value"})


def test_no_implicit_dotenv_or_phoenix_config(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("MODEL_NAME=leaked\n")
    (tmp_path / "config.dev.yml").write_text("PHOENIX_API_KEY: secret\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MODEL_NAME", "environment-leak")
    settings = load_settings(config={}, environ={})
    assert settings.agent.model_name not in {"leaked", "environment-leak"}
    from agent_config import load_agent_settings
    assert load_agent_settings({"MODEL_NAME": "explicit"}).phoenix_api_key is None


def test_explicit_dotenv_does_not_mutate_environment(tmp_path, monkeypatch):
    path = tmp_path / "local.env"
    path.write_text("MODEL_NAME=dotenv\nMODEL_API_KEY=local-key\n")
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    settings = load_settings(config={"MODEL_NAME": "yaml"}, environ={"MODEL_TIMEOUT_SECONDS": "12"}, dotenv_path=path)
    assert settings.agent.model_name == "yaml"
    assert settings.agent.model_api_key == "local-key"
    assert settings.agent.model_timeout_seconds == 12
    assert "MODEL_API_KEY" not in os.environ


def test_profile_selection_and_common_merge(tmp_path):
    (tmp_path / "config.yml").write_text("service:\n  MODEL_NAME: common\n  MODEL_MAX_RETRIES: 0\n")
    (tmp_path / "config.dev.yml").write_text("service:\n  MODEL_NAME: development\n")
    settings = load_settings(environ={"APP_ENV": "dev", "MODEL_NAME": "env"}, root=tmp_path)
    assert settings.agent.model_name == "development"
    assert settings.agent.model_max_retries == 0
    with pytest.raises(ConfigurationError, match="Missing selected"):
        load_settings(environ={"APP_ENV": "stg"}, root=tmp_path)


def test_explicit_missing_config_fails(tmp_path):
    with pytest.raises(ConfigurationError, match="Cannot read YAML"):
        load_settings(config_path=tmp_path / "missing.yml", environ={})


def test_production_requires_explicit_databases():
    with pytest.raises(ConfigurationError, match="Deployment requires"):
        load_settings(config={}, environ={}, profile="prd")


@pytest.mark.parametrize("key,value", [
    ("EXECUTOR_TLS_VERIFY", "not-bool"), ("CHECKPOINT_POOL_MIN_SIZE", 8),
    ("DATABASE_POOL_SIZE", 0), ("DATABASE_MAX_OVERFLOW", -1),
    ("SSE_POLL_INTERVAL_SECONDS", 0), ("GRAPH_CHECKPOINTER", "unsupported"),
    ("MODEL_NAME", None), ("MODEL_MOCK_DELAY_MS", -1),
])
def test_invalid_values_fail_without_fallback(key, value):
    with pytest.raises(ConfigurationError):
        local_settings(**{key: value})


def test_workflow_store_disabled_only_explicitly():
    assert local_settings().workflow_database_url is not None
    assert local_settings(WORKFLOW_PERSISTENCE_ENABLED=False).workflow_database_url is None
    with pytest.raises(ConfigurationError):
        local_settings(WORKFLOW_DATABASE_URL="")


def test_snapshot_is_shared_and_frozen():
    settings = configure(local_settings())
    from agent_config import load_agent_settings
    from config import settings as api_settings
    assert get_settings() is settings
    assert load_agent_settings() is settings.agent
    assert api_settings.database_url == settings.api.database_url
    with pytest.raises(ValidationError):
        settings.api.database_url = "new"
    with pytest.raises(RuntimeError, match="already initialized"):
        configure(local_settings())


def test_safe_summary_omits_values():
    settings = local_settings(MODEL_API_KEY="super-secret", DATABASE_URL="postgresql+asyncpg://user:password@host/db")
    text = json.dumps(settings.summary())
    assert "super-secret" not in text and "password" not in text and "postgresql" not in text


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

    app, router = FastAPI(lifespan=platform_lifespan), APIRouter(lifespan=router_lifespan)
    attach_service(app, local_settings(), router=router, background_factories={"test-worker": worker}, close_resources=close)
    with TestClient(app):
        assert app.state.service_runtime.ready
        assert events == ["platform-start", "router-start", "worker-start"]
    assert events == ["platform-start", "router-start", "worker-start", "worker-stop",
                      "resources-close", "router-stop", "platform-stop"]


def test_double_registration_is_rejected():
    app, settings = FastAPI(), local_settings()
    attach_service(app, settings, router=APIRouter(), background_factories={}, close_resources=AsyncMock())
    with pytest.raises(RuntimeError, match="already attached"):
        attach_service(app, settings, router=APIRouter(), background_factories={}, close_resources=AsyncMock())


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
    attach_service(app, local_settings(), router=APIRouter(),
                   background_factories={"sibling": sibling, "failure": failure}, close_resources=close)
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
    import app.core.database as database
    assert database._engine is None
    monkeypatch.setattr(database, "create_async_engine", lambda *a, **kw: pytest.fail("Unexpected DB engine"))
    app = create_app(local_settings())
    operations = app.openapi()["paths"]
    assert "/api/v1/projects" in operations
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/service/ready").json() == {"ready": True}
    assert database._engine is None


def test_root_entrypoint_with_src_already_on_pythonpath():
    project_root = ROOT
    result = subprocess.run([sys.executable, str(project_root / "app.py"), "--check-config"],
                            cwd=project_root, env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["profile"] == "dev"


@pytest.mark.asyncio
async def test_shutdown_deadline_reports_worker_that_ignores_cancellation():
    release = asyncio.Event()

    async def slow_cleanup():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()

    runtime = BackgroundRuntime({"slow-cleanup": slow_cleanup}, .01)
    await runtime.start()
    try:
        with pytest.raises(RuntimeError, match="shutdown deadline exceeded: slow-cleanup"):
            await runtime.stop()
        assert not runtime.ready
    finally:
        release.set()
        await asyncio.gather(*runtime.tasks.values())
        await runtime.stop()


@pytest.mark.asyncio
async def test_all_owned_resources_close_when_one_cleanup_fails(monkeypatch):
    from service_bootstrap import _close_resources
    import app.core.database as database
    import app.agent_worker.api_bridge as bridge
    from app.services.agent_graph_service import runtime

    shutdown = AsyncMock(side_effect=RuntimeError("cleanup failed"))
    close_bridge, close_database = AsyncMock(), AsyncMock()
    monkeypatch.setattr(runtime, "shutdown", shutdown)
    monkeypatch.setattr(bridge, "close_api_worker_bridge", close_bridge)
    monkeypatch.setattr(database, "close_database", close_database)
    with pytest.raises(RuntimeError, match="cleanup failed"):
        await _close_resources()
    close_bridge.assert_awaited_once()
    close_database.assert_awaited_once()


def test_local_migration_guard_checks_resolved_yaml_target():
    import runpy
    bootstrap = runpy.run_path(str(ROOT / "scripts/local/bootstrap.py"))
    settings = load_settings(config={"DATABASE_URL": "postgresql+asyncpg://remote/chat_app"},
                             environ={"DATABASE_URL": "postgresql+asyncpg://postgres/chat_app"})
    configure(settings)
    assert bootstrap["resolved_targets"]()["DATABASE_URL"] == settings.api.database_url
    with pytest.raises(RuntimeError, match="DATABASE_URL must point at the local Compose"):
        bootstrap["validate_local_targets"]()


@pytest.mark.parametrize("values", [
    {"EW_POLL_SECONDS": float("inf")}, {"LLM_TEMPERATURE": float("nan")},
    {"MODEL_PROVIDER": ""}, {"MOCK_DATA_ROOT": None},
])
def test_nonfinite_or_empty_explicit_values_are_rejected(values):
    with pytest.raises(ConfigurationError):
        local_settings(**values)
