"""One settings snapshot for API, Agent and Executor event consumers.

Only this module reads configuration sources. Explicit mappings are isolated
from process environment and local files, which also makes tests deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from threading import RLock
from types import MappingProxyType
from typing import Any, Mapping
import os
import json

import yaml
from pydantic import ValidationError
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parent.parent
ALIASES = {
    "MODEL_PROVIDER": ("LLM_PROVIDER",),
    "MODEL_NAME": ("LLM_MODEL_NAME",),
    "MODEL_API_KEY": ("LLM_API_KEY",),
    "API_BASE_URL": ("LLM_API_BASE_URL",),
    "MODEL_TEMPERATURE": ("LLM_TEMPERATURE",),
    "MODEL_TIMEOUT_SECONDS": ("LLM_TIMEOUT_SECONDS",),
    "MODEL_MAX_RETRIES": ("LLM_MAX_RETRIES",),
    "MODEL_MAX_OUTPUT_TOKENS": ("LLM_MAX_OUTPUT_TOKENS",),
    "MODEL_ENABLE_THINKING": ("LLM_ENABLE_THINKING",),
    "MODEL_STRUCTURED_OUTPUT_MODE": ("LLM_STRUCTURED_OUTPUT_MODE",),
    "CHECKPOINT_DB_URI": ("AGENT_CHECKPOINT_DATABASE_URL",),
    "EXECUTOR_EXECUTIONS_PATH": ("EXECUTOR_JOBS_PATH",),
}
CANONICAL = {alias: key for key, aliases in ALIASES.items() for alias in aliases}
GROUPS = {"runtime", "database", "checkpoint", "llm", "agent", "executor", "events", "storage", "diagnostics"}
# Extra settings consumed by the legacy Agent adapter, outside the API model.
AGENT_KEYS = set("""
APP_ENV MODEL_MOCK_DELAY_MS MODEL_TEMPERATURE MODEL_PROVIDER MODEL_NAME
MODEL_API_KEY API_BASE_URL MODEL_TIMEOUT_SECONDS MODEL_MAX_RETRIES
MODEL_ENABLE_THINKING MODEL_STRUCTURED_OUTPUT_MODE
CHECKPOINT_DB_URI CHECKPOINT_SETUP_ON_START CHECKPOINT_POOL_MIN_SIZE
CHECKPOINT_POOL_MAX_SIZE CHECKPOINT_POOL_TIMEOUT_SECONDS LANGGRAPH_STRICT_MSGPACK
EXECUTOR_BASE_URL EXECUTOR_TLS_VERIFY EXECUTOR_RUNTIME_PROFILE
EXECUTOR_EXECUTIONS_PATH EXECUTOR_OPERATIONS_PATH EXECUTOR_EXECUTION_PATH
EXECUTOR_RESULT_PATH EXECUTOR_NOTEBOOK_PATH EXECUTOR_FINALIZE_PATH
EXECUTOR_CANCEL_PATH EXECUTOR_ARTIFACTS_PATH EXECUTOR_SHARED_INPUT_ROOT
EXECUTOR_RESULT_READ_MODE EXECUTOR_SHARED_RESULT_ROOT DATA_MOCK
EXECUTOR_SOURCE_TYPE EXECUTOR_REPORT_SOURCE_TYPE EXECUTOR_REPORT_APPEND_TO_NOTEBOOK
EXECUTOR_HTTP_MAX_CONNECTIONS EXECUTOR_HTTP_CONNECT_TIMEOUT_SECONDS
EXECUTOR_HTTP_POOL_TIMEOUT_SECONDS EXECUTOR_HTTP_MAX_RESPONSE_BYTES
EXECUTOR_TIMEOUT_SECONDS EXECUTOR_OPERATION_TIMEOUT_SECONDS
EXECUTOR_OPERATION_WAIT_TIMEOUT_SECONDS EXECUTOR_SUBMIT_ENABLED
DEMO_ARTIFACTS_ENABLED DEMO_ARTIFACTS_ROOT PHOENIX_ENDPOINT
PHOENIX_PROJECT_NAME PHOENIX_API_KEY MAX_WORKFLOW_REVISIONS
WORKFLOW_RECOMMENDATION_ENABLED WORKFLOW_SIMILARITY_SCORE
    MAX_PLAN_CANDIDATES AGENT_HISTORY_MESSAGE_LIMIT ANALYSIS_DATASETS AGENT_DISCOVERY_MAX_ROUNDS
    AGENT_OBSERVATION_MAX_CHARS AGENT_MAX_OPERATIONS
    AGENT_REPAIR_LEVEL AGENT_REPAIR_LEVEL_LIMIT AGENT_MAX_REPAIR_ATTEMPTS
    AGENT_FREE_PLAN_ENABLED AGENT_FREE_PLAN_REQUIRE_APPROVAL AGENT_MAX_PLAN_REVISIONS
""".split())
EXTRA_KEYS = {
    "MODEL_CATALOG", "DEFAULT_MODEL",
    "WORKFLOW_DATABASE_URL", "WORKFLOW_PERSISTENCE_ENABLED", "MOCK_DATA_ROOT",
    "EVENT_WORKER_ENABLED", "SHUTDOWN_TIMEOUT_SECONDS", "RUN_DIAGNOSTICS_DIR",
    "RUN_DIAGNOSTICS_STALL_SECONDS", "SHUTDOWN_DRAIN_SECONDS",
}


class ConfigurationError(ValueError):
    """Messages contain setting names, never input values or credentials."""


def _key(name: str) -> str:
    upper = name.upper()
    return CANONICAL.get(upper, upper)


def _api_key(name: str, info: Any) -> str:
    alias = info.validation_alias
    if hasattr(alias, "choices"):
        return _key(alias.choices[0])
    return _key(alias if isinstance(alias, str) else name)


def _flatten(document: Mapping[str, Any]) -> dict[str, Any]:
    # Platform YAML can contain unrelated logger/Gaia settings. Service-owned
    # values live under `service`; without that key, this is a service mapping.
    section = document.get("service", document)
    if not isinstance(section, Mapping):
        raise ConfigurationError("service must be a mapping")
    output: dict[str, Any] = {}
    for name, value in section.items():
        if not isinstance(name, str):
            raise ConfigurationError("Configuration keys must be strings")
        if name in GROUPS and isinstance(value, Mapping):
            entries = value.items()
        else:
            entries = [(name, value)]
        for child, item in entries:
            if not isinstance(child, str):
                raise ConfigurationError("Configuration keys must be strings")
            if child.upper() in output:
                raise ConfigurationError(f"Duplicate setting: {child.upper()}")
            output[child.upper()] = item
    return output


def _normalize(values: Mapping[str, Any], known: set[str], *, strict: bool) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for raw, value in values.items():
        key = _key(raw)
        if key not in known:
            if strict:
                raise ConfigurationError(f"Unknown service setting: {raw}")
            continue
        if key in result and result[key] != value:
            raise ConfigurationError(f"Conflicting aliases for {key}")
        result[key] = value
    return result


def _read_yaml(path: Path) -> Mapping[str, Any]:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        raise ConfigurationError(f"Cannot read YAML configuration: {path.name}") from None
    if document is None:
        return {}
    if not isinstance(document, Mapping):
        raise ConfigurationError(f"Configuration must be a mapping: {path.name}")
    return document


def read_local_env(path: Path) -> dict[str, str]:
    """Read an explicitly selected local file; never mutate os.environ."""
    from dotenv import dotenv_values
    if not path.is_file():
        raise ConfigurationError("Explicit local dotenv file does not exist")
    # No variable expansion from process state; callers pass the full source.
    return {k: v for k, v in dotenv_values(path, interpolate=False).items() if v is not None}


def _boolean(value: Any, key: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in {"true", "1", "yes", "on"}:
        return True
    if isinstance(value, str) and value.lower() in {"false", "0", "no", "off"}:
        return False
    raise ConfigurationError(f"Invalid boolean setting: {key}")


def _postgres_url(value: str, key: str) -> str:
    try:
        url = make_url(value)
        if url.get_backend_name() != "postgresql" or not url.database:
            raise ValueError()
        return url.set(drivername="postgresql").render_as_string(hide_password=False)
    except Exception:
        # SQLAlchemy's error may include the original URL; do not expose it.
        raise ConfigurationError(f"Invalid PostgreSQL URL: {key}") from None


@dataclass(frozen=True, repr=False)
class ServiceSettings:
    api: Any
    agent: Any
    worker: Any
    profile: str
    event_worker_enabled: bool
    workflow_database_url: str | None = field(repr=False)
    mock_data_root: Path
    shutdown_timeout_seconds: float
    shutdown_drain_seconds: float
    diagnostics_dir: Path | None
    diagnostics_stall_seconds: float
    sources: Mapping[str, str]

    def summary(self) -> dict[str, Any]:
        """Safe for startup logs / --check-config; never dump values or DSNs."""
        return {
            "profile": self.profile,
            "agent_worker_enabled": self.api.agent_worker_enabled,
            "agent_worker_concurrency": self.api.agent_worker_concurrency,
            "event_worker_enabled": self.event_worker_enabled,
            "shutdown_drain_seconds": self.shutdown_drain_seconds,
            "shutdown_timeout_seconds": self.shutdown_timeout_seconds,
            "settings_sources": dict(self.sources),
        }


def load_settings(
    *,
    config: Mapping[str, Any] | None = None,
    environ: Mapping[str, Any] | None = None,
    config_path: Path | None = None,
    profile: str | None = None,
    dotenv_path: Path | None = None,
    root: Path = ROOT,
) -> ServiceSettings:
    """YAML > environment > explicit local dotenv > defaults, per field.

    Passing `config={}` disables all YAML discovery. Passing `environ={}`
    disables process environment. Neither source ever auto-loads .env.
    """
    from config import Settings as APISettings
    from agent_config import _agent_settings_from_mapping
    from event_worker_settings import Settings as WorkerSettings

    env = dict(os.environ if environ is None else environ)
    selected = profile or env.get("APP_ENV", "dev")
    selected = {"development": "dev", "staging": "stg", "production": "prd"}.get(selected, selected)
    if selected not in {"dev", "stg", "prd"}:
        raise ConfigurationError("APP_ENV must select dev, stg or prd")
    api_fields = {name: _api_key(name, info) for name, info in APISettings.model_fields.items()}
    worker_keys = {"EW_" + name.upper() for name in WorkerSettings.model_fields}
    known = set(api_fields.values()) | worker_keys | AGENT_KEYS | EXTRA_KEYS
    sources: dict[str, str] = {}
    merged: dict[str, Any] = {}

    def overlay(values: Mapping[str, Any], source: str, strict: bool = False) -> None:
        normalized = _normalize(values, known, strict=strict)
        merged.update(normalized)
        sources.update({key: source for key in normalized})

    if dotenv_path is not None:
        if selected != "dev":
            raise ConfigurationError("Local dotenv is allowed only in dev")
        overlay(read_local_env(dotenv_path), "local dotenv")
    overlay(env, "environment")
    if config is not None:
        if config_path is not None:
            raise ConfigurationError("Choose config mapping or config_path, not both")
        overlay(_flatten(config), "config mapping", True)
    elif config_path is not None or env.get("SERVICE_CONFIG_FILE"):
        path = Path(config_path or env["SERVICE_CONFIG_FILE"])
        overlay(_flatten(_read_yaml(path)), "config file", True)
    else:
        common, specific = root / "config.yml", root / f"config.{selected}.yml"
        if common.is_file():
            overlay(_flatten(_read_yaml(common)), "config.yml", True)
        if specific.is_file():
            overlay(_flatten(_read_yaml(specific)), f"config.{selected}.yml", True)
        elif "APP_ENV" in env or profile is not None:
            raise ConfigurationError(f"Missing selected config.{selected}.yml")
    # Environment selection is bootstrap input, not a value overridden by YAML.
    merged["APP_ENV"] = selected
    if selected in {"stg", "prd"}:
        missing = {"DATABASE_URL", "CHECKPOINT_DB_URI"} - merged.keys()
        if missing:
            raise ConfigurationError("Deployment requires explicit " + ", ".join(sorted(missing)))
    if "MODEL_PROVIDER" in merged and merged["MODEL_PROVIDER"] not in {"mock", "openai_compatible"}:
        raise ConfigurationError("Unsupported MODEL_PROVIDER")
    api_input = {name: merged[key] for name, key in api_fields.items() if key in merged}
    try:
        api = APISettings.model_validate(api_input)
    except ValidationError as exc:
        fields = ", ".join(".".join(map(str, e["loc"])) for e in exc.errors())
        raise ConfigurationError(f"Invalid API settings: {fields}") from None
    # Shared defaults are chosen once, rather than diverging in two loaders.
    for name, key in api_fields.items():
        merged.setdefault(key, getattr(api, name))
        sources.setdefault(key, "default")
    agent_env: dict[str, Any] = {}
    optional = {"MODEL_API_KEY", "API_BASE_URL", "MODEL_ENABLE_THINKING",
                "PHOENIX_ENDPOINT", "PHOENIX_API_KEY"}
    for key in AGENT_KEYS:
        if key not in merged:
            continue
        value = merged[key]
        if value is None and key not in optional:
            raise ConfigurationError(f"Null is not allowed for {key}")
        agent_env[key] = (json.dumps(value) if key == "ANALYSIS_DATASETS" and isinstance(value, Mapping)
                          else None if value is None else str(value))
    try:
        agent = _agent_settings_from_mapping(agent_env)
    except (ValueError, TypeError, AttributeError):
        raise ConfigurationError("Invalid Agent settings; check types, enums and numeric bounds") from None
    if "MODEL_CATALOG" in merged and merged["MODEL_CATALOG"] is None:
        raise ConfigurationError("MODEL_CATALOG must be a non-empty mapping")
    if "DEFAULT_MODEL" in merged and not isinstance(merged["DEFAULT_MODEL"], str):
        raise ConfigurationError("DEFAULT_MODEL must be an alias")
    from service_runtime.model_selection import build_catalog, ModelSelectionError
    try:
        agent = replace(agent, model_catalog=build_catalog(
            agent, merged.get("MODEL_CATALOG"), merged.get("DEFAULT_MODEL")))
    except ModelSelectionError:
        raise ConfigurationError("Invalid MODEL_CATALOG or DEFAULT_MODEL") from None
    if agent.model_provider not in {"mock", "openai_compatible"}:
        raise ConfigurationError("Unsupported MODEL_PROVIDER")
    if not 0 <= agent.workflow_similarity_score <= 1 or agent.max_workflow_revisions < 1:
        raise ConfigurationError("Invalid Workflow recommendation/revision settings")
    if not 0 < agent.executor_timeout_seconds < float("inf"):
        raise ConfigurationError("EXECUTOR_TIMEOUT_SECONDS must be finite and positive")
    # Role-specific URLs can differ. Missing Worker URLs derive explicitly from
    # API settings; invalid supplied URLs never trigger a fallback.
    worker_input = {name: merged["EW_" + name.upper()] for name in WorkerSettings.model_fields
                    if "EW_" + name.upper() in merged}
    worker_input.setdefault("database_url", _postgres_url(api.database_url, "DATABASE_URL"))
    worker_input.setdefault("redis_url", api.redis_url)
    worker_input.setdefault("executor_base_url", agent.executor_base_url)
    worker_input.setdefault("namespace", "dtest-agent")
    for key, origin in {
        "EW_DATABASE_URL": "derived from DATABASE_URL",
        "EW_REDIS_URL": "derived from REDIS_URL",
        "EW_EXECUTOR_BASE_URL": "derived from EXECUTOR_BASE_URL",
        "WORKFLOW_DATABASE_URL": "derived from EW_DATABASE_URL",
    }.items():
        sources.setdefault(key, origin)
    try:
        worker = WorkerSettings.model_validate(worker_input)
    except ValidationError as exc:
        fields = ", ".join(".".join(map(str, e["loc"])) for e in exc.errors())
        raise ConfigurationError(f"Invalid event Worker settings: {fields}") from None
    _postgres_url(worker.database_url, "EW_DATABASE_URL")
    _postgres_url(agent.checkpoint_db_uri, "CHECKPOINT_DB_URI")
    if not worker.redis_url.startswith(("redis://", "rediss://")):
        raise ConfigurationError("Invalid Redis URL: EW_REDIS_URL")
    workflow_enabled = _boolean(merged.get("WORKFLOW_PERSISTENCE_ENABLED", True), "WORKFLOW_PERSISTENCE_ENABLED")
    workflow_url = merged.get("WORKFLOW_DATABASE_URL", worker.database_url)
    if workflow_enabled:
        workflow_url = _postgres_url(workflow_url, "WORKFLOW_DATABASE_URL")
    try:
        shutdown = float(merged.get("SHUTDOWN_TIMEOUT_SECONDS", 25))
        drain = float(merged.get("SHUTDOWN_DRAIN_SECONDS", 20))
        stall = float(merged.get("RUN_DIAGNOSTICS_STALL_SECONDS", 5))
        if not 0 < shutdown < float("inf") or not 1 <= stall < float("inf") or not 0 <= drain < float("inf"):
            raise ValueError()
    except (ValueError, TypeError):
        raise ConfigurationError("Invalid shutdown/diagnostics timeout") from None
    for key in known:
        sources.setdefault(key, "default")
    sources["APP_ENV"] = "bootstrap selection"
    mock_root = merged.get("MOCK_DATA_ROOT", "/workspace/pv")
    if not isinstance(mock_root, (str, Path)) or not str(mock_root).strip():
        raise ConfigurationError("Invalid path setting: MOCK_DATA_ROOT")
    return ServiceSettings(
        api=api, agent=agent, worker=worker, profile=selected,
        event_worker_enabled=_boolean(merged.get("EVENT_WORKER_ENABLED", False), "EVENT_WORKER_ENABLED"),
        workflow_database_url=workflow_url if workflow_enabled else None,
        mock_data_root=Path(mock_root),
        shutdown_timeout_seconds=shutdown, shutdown_drain_seconds=drain,
        diagnostics_dir=Path(merged["RUN_DIAGNOSTICS_DIR"]) if merged.get("RUN_DIAGNOSTICS_DIR") else None,
        diagnostics_stall_seconds=stall, sources=MappingProxyType(sources),
    )


_snapshot: ServiceSettings | None = None
_lock = RLock()


def get_settings() -> ServiceSettings:
    global _snapshot
    with _lock:
        if _snapshot is None:
            _snapshot = load_settings()
        return _snapshot


def configure(settings: ServiceSettings) -> ServiceSettings:
    """Install before importing consumers; changing a running process is rejected."""
    global _snapshot
    with _lock:
        if _snapshot is not None and _snapshot is not settings:
            raise RuntimeError("Settings already initialized; restart to change configuration")
        _snapshot = settings
        return settings
