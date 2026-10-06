"""Corporate flat YAML, file selection and lifecycle ownership regressions."""
import json
from pathlib import Path
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
import yaml

import service_settings
from service_settings import ConfigurationError, load_settings
from service_runtime.configuration_files import initialize_profile, yaml_document

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(service_settings, "_snapshot", None)


def test_default_local_and_each_profile_read_exactly_one_file(tmp_path):
    (tmp_path / "config.yml").write_text("PORT: 8000\nMODEL_MAX_RETRIES: 7\n")
    for profile in ("dev", "stg", "prd"):
        (tmp_path / f"config.{profile}.yml").write_text(
            f"PORT: 5000\nDATABASE_URL: postgresql+asyncpg://db/api\nCHECKPOINT_DB_URI: postgresql://db/checkpoints\n")
        settings = load_settings(root=tmp_path, environ={"APP_ENV": profile, "MODEL_MAX_RETRIES": "3"})
        assert settings.profile == profile and settings.api.server_port == 5000
        assert settings.agent.model_max_retries == 3  # never local7
        assert settings.sources["MODEL_MAX_RETRIES"] == "environment"
    local = load_settings(root=tmp_path, environ={})
    assert local.profile == "local" and local.api.server_port == 8000
    assert local.agent.model_max_retries == 7


def test_profile_selection_does_not_even_parse_a_broken_local_file(tmp_path):
    (tmp_path / "config.yml").write_text("[broken yaml")
    (tmp_path / "config.dev.yml").write_text("PORT: 5000\n")
    assert load_settings(root=tmp_path, profile="dev", environ={}).api.server_port == 5000
    (tmp_path / "config.dev.yml").unlink()
    with pytest.raises(ConfigurationError, match="Missing selected config.dev.yml"):
        load_settings(root=tmp_path, profile="dev", environ={})


def test_flat_template_keys_override_legacy_env_without_changing_platform_contract():
    template = yaml.safe_load((ROOT / "config.example.yml").read_text())
    template.update(PORT=18110, PRIVATE_LLM_MODEL_NAME="chosen", PRIVATE_LLM_ENDPOINT="http://model/v1",
                    PRIVATE_LLM_API_KEY="private-test-value", RECURSION_LIMIT=77,
                    ACTIVE_MULTI_TURN=False, SET_MAX_HISTORY=0, ACTIVE_TRACE=False,
                    SYSTEM_ADMIN="not-an-admin-role", API_TOKEN="not-a-login-token", DEFAULT_WORKFLOW="platform-entry")
    settings = load_settings(config=template, environ={"SERVER_PORT": "1", "MODEL_NAME": "old",
                          "API_BASE_URL": "http://old/v1", "MODEL_API_KEY": "old-key"})
    assert settings.api.server_port == 18110
    assert settings.api.llm_model_name == settings.agent.model_name == "chosen"
    assert settings.api.llm_api_base_url == settings.agent.api_base_url == "http://model/v1"
    assert settings.agent.model_api_key == "private-test-value"
    assert settings.agent.recursion_limit == 77
    assert not settings.agent.active_multi_turn and settings.agent.set_max_history == 0
    assert not settings.agent.active_trace
    assert not {"SYSTEM_ADMIN", "API_TOKEN", "DEFAULT_WORKFLOW"} & settings.inputs.keys()
    assert "private-test-value" not in json.dumps(settings.summary())


def test_flat_root_is_not_dropped_when_a_legacy_service_block_is_present():
    settings = load_settings(config={"PORT": 5010, "PRIVATE_LLM_MODEL_NAME": "root",
                            "service": {"agent": {"max_plan_candidates": 2}}}, environ={})
    assert settings.api.server_port == 5010 and settings.agent.model_name == "root"
    assert settings.agent.max_plan_candidates == 2


@pytest.mark.parametrize("values", [
    {"PORT": 5000, "SERVER_PORT": 8000},
    {"PRIVATE_LLM_ENDPOINT": "http://a/v1", "API_BASE_URL": "http://b/v1"},
    {"PRIVATE_LLM_MODEL_NAME": "a", "LLM_MODEL_NAME": "b"},
])
def test_conflicting_template_and_legacy_aliases_are_rejected(values):
    with pytest.raises(ConfigurationError, match="Conflicting aliases"):
        load_settings(config=values, environ={})


def test_duplicate_yaml_is_rejected_without_exposing_value(tmp_path):
    file = tmp_path / "config.yml"
    file.write_text("PRIVATE_LLM_API_KEY: secret-a\nPRIVATE_LLM_API_KEY: secret-b\n")
    with pytest.raises(ConfigurationError, match="Duplicate YAML setting") as error:
        load_settings(root=tmp_path, environ={})
    assert "secret-a" not in str(error.value) and "secret-b" not in str(error.value)


@pytest.mark.parametrize("values", [
    {"RECURSION_LIMIT": 0}, {"RECURSION_LIMIT": True}, {"SET_MAX_HISTORY": -1},
    {"SET_MAX_HISTORY": 101}, {"ACTIVE_MULTI_TURN": "invalid"}, {"ACTIVE_TRACE": None},
    {"AGENT_HISTORY_MESSAGE_LIMIT": 40},
])
def test_invalid_or_removed_history_settings_never_fall_back(values):
    with pytest.raises(ConfigurationError):
        load_settings(config=values, environ={"RECURSION_LIMIT": "100", "SET_MAX_HISTORY": "6"})


def test_local_initialization_and_export_use_flat_template_names(tmp_path):
    (tmp_path / "config.example.yml").write_text((ROOT / "config.example.yml").read_text())
    target = initialize_profile("local", root=tmp_path)
    assert target.name == "config.yml" and target.stat().st_mode & 0o777 == 0o600
    snapshot = load_settings(root=tmp_path, environ={})
    exported = yaml_document(snapshot.inputs)
    assert "service" not in exported and "PORT" in exported and "PRIVATE_LLM_ENDPOINT" in exported
    assert not {"SERVER_PORT", "MODEL_NAME", "API_BASE_URL", "MODEL_API_KEY"} & exported.keys()
    assert load_settings(config=exported, environ={}).agent == snapshot.agent
    with pytest.raises(FileExistsError):
        initialize_profile("local", root=tmp_path)


def test_copy_block_contains_only_service_owned_settings():
    extra = yaml.safe_load((ROOT / "config.service.example.yml").read_text())
    assert not service_settings.PLATFORM_ONLY_KEYS & extra.keys()
    assert not {"PORT", "PRIVATE_LLM_MODEL_NAME", "PRIVATE_LLM_ENDPOINT", "PRIVATE_LLM_API_KEY",
                "RECURSION_LIMIT", "ACTIVE_MULTI_TURN", "SET_MAX_HISTORY", "ACTIVE_TRACE",
                "PHOENIX_ENDPOINT", "PHOENIX_API_KEY"} & extra.keys()
    template = {"PORT": 5000, "PRIVATE_LLM_MODEL_NAME": "corporate", "PRIVATE_LLM_ENDPOINT": "http://llm/v1"}
    settings = load_settings(config={**template, **extra}, environ={})
    assert settings.agent.model_name == "corporate" and settings.api.server_port == 5000


@pytest.mark.parametrize("platform", [True, False])
def test_phoenix_is_owned_by_exactly_one_lifecycle(monkeypatch, platform):
    import api_service.observability.phoenix as tracing
    from service_bootstrap import create_app
    setup, shutdown = Mock(), Mock()
    monkeypatch.setattr(tracing, "setup_phoenix", setup)
    monkeypatch.setattr(tracing, "shutdown_phoenix", shutdown)
    settings = load_settings(config={"AGENT_WORKER_ENABLED": False, "EVENT_WORKER_ENABLED": False,
        "TASK_RECONCILER_ENABLED": False, "SHUTDOWN_DRAIN_SECONDS": 0, "PHOENIX_ENDPOINT": "http://phoenix/v1/traces"}, environ={})
    app = create_app(settings, platform_app=FastAPI() if platform else None)
    with TestClient(app):
        assert setup.call_count == (0 if platform else 1)
    assert shutdown.call_count == (0 if platform else 1)


def test_active_trace_false_does_not_register_a_local_exporter(monkeypatch):
    import api_service.observability.phoenix as tracing
    register = Mock()
    monkeypatch.setattr(tracing, "register", register)
    settings = load_settings(config={"ACTIVE_TRACE": False, "PHOENIX_ENDPOINT": "http://phoenix/v1/traces"}, environ={})
    assert tracing.setup_phoenix(settings.agent) is None
    register.assert_not_called()


@pytest.mark.asyncio
async def test_user_and_executor_execution_configs_get_the_same_recursion_budget(monkeypatch):
    from api_service.services.agent_graph_service import graph_config
    from api_service.runs.graph_invocation import GraphInvocation
    service_settings.configure(load_settings(config={"RECURSION_LIMIT": 73}, environ={}))
    assert graph_config("session", "run")["recursion_limit"] == 73
    class Graph:
        async def ainvoke(self, value, **kwargs):
            return kwargs["config"]
    original = {"configurable": {"thread_id": "executor-session"}}
    config = await GraphInvocation(Graph()).invoke(None, original)
    assert config["recursion_limit"] == 73 and config["configurable"] == original["configurable"]
    assert "recursion_limit" not in original
