"""Shared template ownership, field-driven additions and typed source boundaries."""
from pathlib import Path
import json

from pydantic import AliasChoices, BaseModel, ConfigDict, Field
import pytest

from service_settings import ConfigurationError, load_settings
from service_runtime.settings_sources import ModelBinding, select_values, source_aliases
from service_runtime.configuration_files import initialize_profile

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("profile", ["local", "dev", "stg", "prd"])
def test_future_platform_keys_do_not_need_a_service_allowlist(tmp_path, profile):
    filename = "config.yml" if profile == "local" else f"config.{profile}.yml"
    (tmp_path / filename).write_text(
        "PORT: 8000\nDATABASE_URL: postgresql+asyncpg://user:password@db/chat_app\n"
        "CHECKPOINT_DB_URI: postgresql://user:password@db/agent\n"
        "IS_SECURITY_SERVICE: true\nS3_FILE_URL_EXPIRES_IN: 3600\n"
        "FUTURE_PLATFORM_EXTENSION: {token: private-token, nested: [1, 2]}\n")
    settings = load_settings(root=tmp_path, profile=profile, environ={})
    assert settings.api.server_port == 8000
    assert settings.unused_config_keys == ("FUTURE_PLATFORM_EXTENSION", "IS_SECURITY_SERVICE", "S3_FILE_URL_EXPIRES_IN")
    assert "private-token" not in json.dumps(settings.summary())
    assert "password" not in json.dumps(settings.summary())
    assert "FUTURE_PLATFORM_EXTENSION" not in settings.inputs


def test_new_field_declares_its_own_key_default_alias_and_validation():
    class ExtensionSettings(BaseModel):
        model_config = ConfigDict(populate_by_name=True)
        slots: int = Field(default=2, ge=1, validation_alias=AliasChoices("NEW_SLOTS", "OLD_SLOTS"))
    binding = ModelBinding("extension", ExtensionSettings)
    aliases = source_aliases((binding,))
    selected, unused = select_values({"OLD_SLOTS": "4", "PLATFORM_EXTENSION": 1}, aliases)
    assert binding.validate(binding.inputs(selected)).slots == 4
    assert binding.validate({}).slots == 2
    assert unused == ["PLATFORM_EXTENSION"]
    with pytest.raises(ConfigurationError, match="NEW_SLOTS"):
        binding.validate({"slots": 0})
    with pytest.raises(ConfigurationError, match="Conflicting aliases"):
        select_values({"NEW_SLOTS": 2, "OLD_SLOTS": 3}, aliases)


def test_native_yaml_collections_and_json_environment_are_equivalent():
    from agent_config import load_agent_settings
    native = load_settings(config={"EXECUTOR_RUNTIME_PROFILE": "default",
        "EXECUTOR_RUNTIME_PROFILES": ["default", "other"], "ACTIVE_MULTI_TURN": False,
        "SET_MAX_HISTORY": 0, "SSO_ALLOWED_RETURN_ROOTS": ["/demo", "/docs"]}, environ={})
    env = load_settings(config={}, environ={"EXECUTOR_RUNTIME_PROFILE": "default",
        "EXECUTOR_RUNTIME_PROFILES": '["default", "other"]', "ACTIVE_MULTI_TURN": "false",
        "SET_MAX_HISTORY": "0", "SSO_ALLOWED_RETURN_ROOTS": '["/demo", "/docs"]'})
    assert native.agent == env.agent
    assert native.sso == env.sso
    assert isinstance(native.inputs["SET_MAX_HISTORY"], int)
    assert isinstance(native.inputs["ACTIVE_MULTI_TURN"], bool)
    assert isinstance(native.inputs["EXECUTOR_RUNTIME_PROFILES"], list)
    assert load_agent_settings({"RECURSION_LIMIT": "73"}).recursion_limit == 73


@pytest.mark.parametrize("values", [
    {"EXECUTOR_RUNTIME_PROFILES": []}, {"EVENT_WORKER_ENABLED": None},
    {"SHUTDOWN_TIMEOUT_SECONDS": True}, {"RECURSION_LIMIT": True},
    {"MOCK_DATA_ROOT": " "}, {"WORKFLOW_DATABASE_URL": ""},
    {"ANALYSIS_DATASETS": "private-invalid-json"},
])
def test_invalid_our_values_never_fall_back(values):
    with pytest.raises(ConfigurationError) as error:
        load_settings(config=values, environ={"SHUTDOWN_TIMEOUT_SECONDS": "25"})
    assert "private-invalid-json" not in str(error.value)


def test_initializer_preserves_unknown_platform_values_without_parsing_them(tmp_path):
    template = (ROOT / "config.example.yml").read_text()
    template += "NEW_PLATFORM_TOKEN: private-value\n"
    (tmp_path / "config.example.yml").write_text(template)
    file = initialize_profile("local", root=tmp_path)
    assert file.read_text() == template
    settings = load_settings(root=tmp_path, environ={})
    assert "NEW_PLATFORM_TOKEN" in settings.unused_config_keys
    assert "private-value" not in str(settings.summary())
