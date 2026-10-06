"""Effective configuration and deployment contracts, without external IO."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock
from contextlib import asynccontextmanager
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
import yaml

import dtest.settings.loader as service_settings
from dtest.settings.loader import ConfigurationError, load_settings
from dtest.settings.retired import (
    REMOVED_SETTINGS,
    REMOVED_INFRASTRUCTURE_SETTINGS,
    REMOVED_EXECUTOR_PATH_SETTINGS,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def snapshot(monkeypatch):
    monkeypatch.setattr(service_settings, "_snapshot", None)


@pytest.mark.parametrize(
    "canonical,legacy,value",
    [
        ("REDIS_URL", "EW_REDIS_URL", "redis://localhost:6379/2"),
        ("EXECUTOR_BASE_URL", "EW_EXECUTOR_BASE_URL", "http://executor:8000"),
    ],
)
def test_one_endpoint_accepts_legacy_spelling_and_config_overrides_env(
    canonical, legacy, value
):
    settings = load_settings(
        config={legacy: value}, environ={canonical: "ignored"}
    )
    if canonical == "REDIS_URL":
        assert settings.redis.redis_url == settings.worker.redis_url == value
    else:
        assert (
            settings.agent.executor_base_url
            == settings.worker.executor_base_url
            == value
        )
    with pytest.raises(ConfigurationError, match=canonical):
        load_settings(
            config={}, environ={canonical: value, legacy: "different"}
        )
    assert load_settings(config={}, environ={canonical: value, legacy: value})


@pytest.mark.parametrize("profile", ["dev", "stg", "prd"])
def test_private_yaml_profiles_define_ports_and_switches_without_dotenv(
    tmp_path, profile
):
    import shutil
    from dtest.settings.files import initialize_profile

    shutil.copy(ROOT / "config.example.yml", tmp_path / "config.yml")
    shutil.copy(
        ROOT / f"config.{profile}.example.yml",
        tmp_path / f"config.{profile}.example.yml",
    )
    initialize_profile(profile, root=tmp_path)
    settings = load_settings(profile=profile, environ={}, root=tmp_path)
    assert settings.api.server_port == 5000
    assert (
        settings.commands.agent_worker_enabled
        and settings.commands.task_reconciler_enabled
    )
    assert settings.event_worker_enabled
    assert settings.agent.executor_submit_enabled == (profile != "dev")
    assert settings.worker.health_port == 0
    # Selected YAML, including false/zero, wins over env.
    env = {
        "SERVER_PORT": "5000",
        "EXECUTOR_SUBMIT_ENABLED": "true",
        "MODEL_MAX_RETRIES": "8",
    }
    effective = load_settings(profile=profile, environ=env, root=tmp_path)
    assert effective.api.server_port == 5000
    assert (
        effective.agent.executor_submit_enabled
        == settings.agent.executor_submit_enabled
    )
    assert effective.agent.model_max_retries == 0


def test_event_worker_default_tracks_agent_but_accepts_explicit_override():
    assert load_settings(config={}, environ={}).event_worker_enabled
    assert not load_settings(
        config={"AGENT_WORKER_ENABLED": False}, environ={}
    ).event_worker_enabled
    assert load_settings(
        config={"AGENT_WORKER_ENABLED": False, "EVENT_WORKER_ENABLED": True},
        environ={},
    ).event_worker_enabled


def test_config_summary_never_exposes_shared_credentials():
    settings = load_settings(
        config={},
        environ={
            "REDIS_URL": "redis://:do-not-print@host:6379",
            "DATABASE_URL": "postgresql+asyncpg://u:do-not-print@host/chat_app",
        },
    )
    assert "do-not-print" not in str(settings.summary())
    assert settings.summary()["server_processes"] == 1
    assert settings.summary()["connection_pool_limits"]["crud"] == 20


@pytest.mark.parametrize(
    "path", ["deploy/dtest-agent.yaml", "cicd/basic/dev/deployment.yml"]
)
def test_deployment_starts_one_container_with_canonical_lifecycle(path):
    docs = list(yaml.safe_load_all((ROOT / path).read_text()))
    deployments = [d for d in docs if d["kind"] == "Deployment"]
    assert len(deployments) == 1
    pod = deployments[0]["spec"]["template"]["spec"]
    assert not pod.get("initContainers")
    assert len(pod["containers"]) == 1
    container = pod["containers"][0]
    assert container["command"] == ["python", "app.py"]
    assert container["readinessProbe"]["httpGet"]["path"] == "/service/ready"
    assert container["livenessProbe"]["httpGet"]["path"] == "/service/live"
    assert pod["terminationGracePeriodSeconds"] >= 20 + 25 + 10
    env = {v["name"]: v.get("value") for v in container.get("env", [])}
    assert "EW_INSTANCE_ID" not in env
    for d in docs:
        if d["kind"] == "Ingress":
            assert d["apiVersion"] == "networking.k8s.io/v1"


def test_integrated_readiness_waits_for_consumer_then_tracks_its_health(
    monkeypatch,
):
    import dtest.bootstrap as service_bootstrap
    from dtest.worker_service.executor_events.telemetry import Telemetry

    worker = type(
        "Worker",
        (),
        {"ready": AsyncMock(return_value=True), "telemetry": Telemetry()},
    )()
    holder = {}

    async def loop():
        await asyncio.Event().wait()

    def factories(settings, stop, *, on_event_worker):
        holder["publish"] = on_event_worker
        return {"executor-event-worker": loop}

    monkeypatch.setattr(service_bootstrap, "_background_factories", factories)
    database = SimpleNamespace(
        execute=AsyncMock(), scalar=AsyncMock(return_value=True)
    )

    @asynccontextmanager
    async def short_session():
        yield database

    monkeypatch.setattr(
        "dtest.infrastructure.database.runtime.short_session", short_session
    )
    settings = load_settings(
        config={
            "AGENT_WORKER_ENABLED": False,
            "TASK_RECONCILER_ENABLED": False,
            "EVENT_WORKER_ENABLED": True,
            "SHUTDOWN_DRAIN_SECONDS": 0,
        },
        environ={},
    )
    app = service_bootstrap.create_app(settings)
    with TestClient(app) as client:
        assert client.get("/service/ready").status_code == 503
        holder["publish"](worker)
        assert client.get("/service/ready").status_code == 200
        metrics = client.get("/service/metrics")
        assert metrics.status_code == 200
        assert (
            "ew_operations" in metrics.text and "python_info" in metrics.text
        )
        database.scalar.return_value = False
        assert client.get("/service/ready").status_code == 503
        database.scalar.return_value = True
        worker.ready.return_value = False
        assert client.get("/service/ready").status_code == 503
        assert client.get("/service/live").status_code == 200
        holder["publish"](None)
        assert client.get("/service/ready").status_code == 503


@pytest.mark.parametrize("profile", ["dev", "prd"])
def test_local_generation_has_one_private_yaml_and_infrastructure_only_env(
    tmp_path, monkeypatch, profile
):
    import runpy
    import shutil

    module = runpy.run_path(str(ROOT / "scripts/local.py"))
    initialize = module["initialize"]
    generated = tmp_path / "workspace/config.compose.yml"
    for name, value in {
        "ROOT": tmp_path,
        "ENV_FILE": tmp_path / ".env.local",
        "APP_CONFIG": generated,
    }.items():
        monkeypatch.setitem(initialize.__globals__, name, value)
    source_file = tmp_path / f"config.{profile}.yml"
    common = ROOT / "config.example.yml"
    shutil.copy(common, tmp_path / "config.yml")
    values = {
        "DATABASE_URL": "postgresql+asyncpg://u:pass@external:15432/chat_app",
        "CHECKPOINT_DB_URI": "postgresql://u:pass@external:15432/agent",
        "REDIS_URL": "redis://external:6379/0",
        "EXECUTOR_BASE_URL": "http://executor",
        "EXECUTOR_SHARED_INPUT_ROOT": "/workspace/shared",
        "EXECUTOR_SHARED_RESULT_ROOT": "/workspace/shared",
        "EXECUTOR_SOURCE_TYPE": "INLINE",
    }
    source_file.write_text(yaml.safe_dump(values))
    before = source_file.read_bytes()
    (tmp_path / ".env").write_text("DATABASE_URL=do-not-read\n")
    (tmp_path / ".env.local.example").write_text("LOCAL_API_PORT=18000\n")
    initialize(profile=profile)
    env = service_settings.read_local_env(tmp_path / ".env.local")
    assert source_file.read_bytes() == before
    assert (tmp_path / ".env").read_text() == "DATABASE_URL=do-not-read\n"
    assert all(key.startswith("LOCAL_") for key in env)
    effective = load_settings(
        config_path=generated, environ={}, profile=env["LOCAL_APP_ENV"]
    )
    assert effective.profile == profile
    assert effective.agent.environment == profile
    assert (
        effective.database.database_url
        == "postgresql+asyncpg://u:pass@postgres:5432/chat_app"
    )
    assert (
        effective.worker.database_url
        == "postgresql://u:pass@postgres:5432/chat_app"
    )
    assert (
        effective.agent.checkpoint_db_uri
        == "postgresql://u:pass@postgres:5432/agent"
    )
    assert effective.redis.redis_url == values["REDIS_URL"]
    assert (
        effective.api.server_host == "0.0.0.0"
        and effective.api.server_port == 8000
    )
    assert effective.agent.executor_source_type == "INLINE"
    assert generated.stat().st_mode & 0o777 == 0o640
    assert env["LOCAL_CONFIG_GID"] == str(generated.stat().st_gid)
    assert (tmp_path / ".env.local").stat().st_mode & 0o777 == 0o600
    assert (
        "CREATE DATABASE agent"
        in (ROOT / "scripts/local/init-databases.sql").read_text()
    )


def test_local_upgrade_drains_only_legacy_worker_in_same_project(monkeypatch):
    import runpy
    import subprocess

    module = runpy.run_path(str(ROOT / "scripts/local.py"))
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(
            args, 0, "legacy-id\n" if args[1] == "ps" else "", ""
        )

    monkeypatch.delenv("COMPOSE_PROJECT_NAME", raising=False)
    monkeypatch.setattr(subprocess, "run", run)
    module["retire_legacy_event_worker"]()
    assert "label=com.docker.compose.project=dtest-agent-local" in calls[0]
    assert "label=com.docker.compose.service=event-worker" in calls[0]
    assert calls[1] == ["docker", "stop", "--time", "70", "legacy-id"]
    assert calls[2] == ["docker", "rm", "legacy-id"]


def test_explicit_event_database_normalizes_driver_from_common_api_url():
    url = "postgresql+asyncpg://user:pass@localhost/chat_app"
    settings = load_settings(
        config={"DATABASE_URL": url, "EW_DATABASE_URL": url}, environ={}
    )
    assert settings.database.database_url == url
    assert (
        settings.worker.database_url
        == "postgresql://user:pass@localhost/chat_app"
    )


def test_configured_worker_identity_is_prefix_not_shared_consumer_identity():
    values = {"EW_INSTANCE_ID": "legacy-fixed-name"}
    first = load_settings(config={}, environ=values)
    second = load_settings(config={}, environ=values)
    assert first.worker.instance_id.startswith("legacy-fixed-name:")
    assert second.worker.instance_id.startswith("legacy-fixed-name:")
    assert first.worker.instance_id != second.worker.instance_id


@pytest.mark.parametrize(
    "url,local",
    [("redis://redis:6379/0", True), ("redis://external:6379/0", False)],
)
def test_local_start_prepares_selected_infrastructure(
    tmp_path, monkeypatch, url, local
):
    import runpy
    import sys

    module = runpy.run_path(str(ROOT / "scripts/local.py"))
    main = module["main"]
    env = tmp_path / ".env.local"
    env.write_text("LOCAL_API_PORT=18000\n")
    private = tmp_path / "compose.yml"
    private.write_text(yaml.safe_dump({"REDIS_URL": url}))
    calls = []
    for name, value in {
        "ENV_FILE": env,
        "APP_CONFIG": private,
        "initialize": lambda *args: None,
        "compose": lambda *args: calls.append(args),
        "retire_legacy_event_worker": lambda: None,
        "smoke": lambda: None,
    }.items():
        monkeypatch.setitem(main.__globals__, name, value)
    monkeypatch.setattr(sys, "argv", ["local.py", "up"])
    main()
    if local:
        assert (
            "--profile",
            "local-redis",
            "up",
            "-d",
            "--wait",
            "postgres",
            "redis",
        ) in calls
    else:
        assert ("up", "-d", "--wait", "postgres") in calls
        assert not any("redis" in call for call in calls)


def test_active_command_worker_requires_same_database_without_exposing_credentials():
    with pytest.raises(ConfigurationError, match="same database") as error:
        load_settings(
            config={
                "DATABASE_URL": "postgresql+asyncpg://u:secret@host/chat_app",
                "EW_DATABASE_URL": "postgresql://u:secret@host/agent",
            },
            environ={},
        )
    assert "secret" not in str(error.value)
    assert load_settings(
        config={
            "DATABASE_URL": "postgresql+asyncpg://u:secret@host/chat_app",
            "EW_DATABASE_URL": "postgresql://u:secret@host:5432/chat_app",
        },
        environ={},
    )
    # Inspection/migration can still resolve the old separate targets with execution disabled.
    assert load_settings(
        config={
            "DATABASE_URL": "postgresql+asyncpg://u:secret@host/chat_app",
            "EW_DATABASE_URL": "postgresql://u:secret@host/agent",
            "AGENT_WORKER_ENABLED": False,
            "EVENT_WORKER_ENABLED": False,
        },
        environ={},
    )


@pytest.mark.parametrize("name", sorted(REMOVED_SETTINGS))
@pytest.mark.parametrize("source", ["config", "env"])
def test_removed_dispatch_settings_fail_with_migration_instructions(
    name, source
):
    with pytest.raises(
        ConfigurationError, match="Removed service setting: " + name
    ):
        load_settings(
            config={name: "obsolete"} if source == "config" else {},
            environ={name: "obsolete"} if source == "env" else {},
        )


@pytest.mark.parametrize("name", sorted(REMOVED_INFRASTRUCTURE_SETTINGS))
@pytest.mark.parametrize("source", ["config", "env"])
def test_removed_infrastructure_settings_fail_without_exposing_values(
    name, source
):
    with pytest.raises(
        ConfigurationError, match="Removed infrastructure API setting: " + name
    ) as error:
        load_settings(
            config={name: "private-value"} if source == "config" else {},
            environ={name: "private-value"} if source == "env" else {},
        )
    assert "private-value" not in str(error.value)


def test_ingress_has_one_limit_and_old_common_spelling_is_only_an_alias():
    settings = load_settings(config={"EW_CONCURRENCY": 3}, environ={})
    assert settings.worker.ingress_concurrency == 3
    assert "concurrency" not in type(settings.worker).model_fields
    assert "dispatch_concurrency" not in type(settings.worker).model_fields
    with pytest.raises(ConfigurationError, match="Conflicting aliases"):
        load_settings(
            config={"EW_CONCURRENCY": 3, "EW_INGRESS_CONCURRENCY": 4},
            environ={},
        )


@pytest.mark.parametrize(
    "base,expected",
    [
        ("http://executor:8080", "/api/v1/executions/test/events"),
        ("http://executor:8080/", "/api/v1/executions/test/events"),
        (
            "http://executor:8080/gateway",
            "/gateway/api/v1/executions/test/events",
        ),
        (
            "http://executor:8080/proxy/executor/",
            "/proxy/executor/api/v1/executions/test/events",
        ),
    ],
)
@pytest.mark.asyncio
async def test_event_history_uses_same_fixed_contract_as_submissions(
    base, expected
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import httpx
    from dtest.worker_service.executor_events.runtime import ExecutorWorker
    from dtest.infrastructure.executor.routes import ExecutorRoute

    settings = load_settings(config={"EXECUTOR_BASE_URL": base}, environ={})
    worker = ExecutorWorker(settings.worker, {"execution.completed"})
    requests = []

    async def history(request):
        requests.append(request)
        return httpx.Response(200, json={"items": [], "has_more": False})

    store = SimpleNamespace(
        scan_candidates=AsyncMock(
            return_value=[
                {
                    "execution_id": "test",
                    "catch_up_version": 1,
                    "caught_up_version": 0,
                    "last_sequence": 0,
                }
            ]
        ),
        advance=AsyncMock(return_value=(0, None)),
        finish_catch_up=AsyncMock(),
        scan_error=AsyncMock(),
    )
    try:
        async with httpx.AsyncClient(
            base_url=base, transport=httpx.MockTransport(history)
        ) as http:
            worker.router.http = http
            worker.router.store = store
            assert await worker.router.once() == 0
        assert len(requests) == 1 and requests[0].url.path == expected
        assert (
            str(requests[0].url).split("?", 1)[0]
            == ExecutorRoute.EXECUTION.url(
                settings.agent.executor_base_url, "test"
            )
            + "/events"
        )
        assert dict(requests[0].url.params) == {
            "after_sequence": "0",
            "limit": "100",
        }
        store.finish_catch_up.assert_awaited_once_with("test", 1)
        store.scan_error.assert_not_awaited()
    finally:
        await worker.http.aclose()
        await worker.redis.aclose()
        await worker.pool.close()


@pytest.mark.parametrize("name", sorted(REMOVED_EXECUTOR_PATH_SETTINGS))
@pytest.mark.parametrize("source", ["config", "env"])
def test_removed_executor_paths_require_root_base_configuration(name, source):
    with pytest.raises(
        ConfigurationError, match="Removed Executor API path setting: " + name
    ) as error:
        load_settings(
            config={name: "private-route"} if source == "config" else {},
            environ={name: "private-route"} if source == "env" else {},
        )
    assert "private-route" not in str(error.value)
