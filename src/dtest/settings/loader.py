"""One settings snapshot for API, Agent and Executor event consumers.

Only this module reads configuration sources. Explicit mappings are isolated
from process environment and local files, which also makes tests deterministic.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from threading import RLock
from types import MappingProxyType
from typing import Any, Mapping
import os
from uuid import uuid4

from sqlalchemy.engine import make_url

from dtest.settings.sources import (
    ConfigurationError,
    ModelBinding,
    flat_values,
    read_local_env,
    read_yaml as _read_yaml,
    select_values,
    source_aliases,
)
from dtest.settings.runtime import RuntimeSettings
from dtest.settings.search import WorkflowSearchSettings

from dtest.settings.retired import check_retired
from dtest.settings.models import ServiceSettings


ROOT = Path(__file__).resolve().parents[3]


def _postgres_url(value: str, key: str) -> str:
    try:
        url = make_url(value)
        if url.get_backend_name() != "postgresql" or not url.database:
            raise ValueError()
        return url.set(drivername="postgresql").render_as_string(
            hide_password=False
        )
    except Exception:
        # SQLAlchemy's error may include the original URL; do not expose it.
        raise ConfigurationError(f"Invalid PostgreSQL URL: {key}") from None


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
    from dtest.settings.api import ApiSettings as APISettings
    from dtest.settings.agent import AgentSettings
    from dtest.settings.events import EventWorkerSettings
    from dtest.settings.auth import SsoSettings

    from dtest.settings.database import DatabaseSettings
    from dtest.settings.worker import WorkerSettings as CommandSettings
    from dtest.settings.redis import RedisSettings
    from dtest.settings.storage import StorageSettings

    storage_binding = ModelBinding("storage", StorageSettings)
    database_binding = ModelBinding("database", DatabaseSettings)
    command_binding = ModelBinding("command Worker", CommandSettings)
    redis_binding = ModelBinding("Redis", RedisSettings)

    api_binding = ModelBinding("API", APISettings)
    agent_binding = ModelBinding("Agent", AgentSettings)
    worker_binding = ModelBinding("event Worker", EventWorkerSettings, "EW_")
    sso_binding = ModelBinding("SSO", SsoSettings, "SSO_")
    search_binding = ModelBinding(
        "Workflow search", WorkflowSearchSettings, "WORKFLOW_SEARCH_"
    )
    runtime_binding = ModelBinding("runtime", RuntimeSettings)
    bindings = (
        storage_binding,
        database_binding,
        command_binding,
        redis_binding,
        api_binding,
        agent_binding,
        worker_binding,
        sso_binding,
        search_binding,
        runtime_binding,
    )
    aliases = source_aliases(bindings)
    env = dict(os.environ if environ is None else environ)
    selected = profile or env.get("APP_ENV", "local")
    selected = {
        "development": "dev",
        "staging": "stg",
        "production": "prd",
    }.get(selected, selected)
    if selected not in {"local", "dev", "stg", "prd"}:
        raise ConfigurationError("APP_ENV must select local, dev, stg or prd")
    merged, sources = {}, {}
    unused_config_keys = []

    def overlay(values, source, *, yaml_source=False):
        check_retired(values)
        normalized, unused = select_values(
            flat_values(values) if yaml_source else values, aliases
        )
        merged.update(normalized)
        sources.update({key: source for key in normalized})
        if yaml_source:
            unused_config_keys.extend(unused)

    if dotenv_path is not None:
        if selected not in {"local", "dev"}:
            raise ConfigurationError(
                "Local dotenv is allowed only in local/dev"
            )
        overlay(read_local_env(dotenv_path), "local dotenv")
    overlay(env, "environment")
    if config is not None:
        if config_path is not None:
            raise ConfigurationError(
                "Choose config mapping or config_path, not both"
            )
        overlay(config, "config mapping", yaml_source=True)
    else:
        explicit = config_path or env.get("SERVICE_CONFIG_FILE")
        name = (
            "config.yml" if selected == "local" else f"config.{selected}.yml"
        )
        path = Path(explicit) if explicit else root / name
        if not explicit and not path.is_file():
            example = (
                "config.example.yml"
                if selected == "local"
                else f"config.{selected}.example.yml"
            )
            raise ConfigurationError(
                f"Missing selected {name}; initialize it from {example}"
            )
        overlay(
            _read_yaml(path),
            "config file" if explicit else name,
            yaml_source=True,
        )
    merged["APP_ENV"] = selected
    sources["APP_ENV"] = "bootstrap selection"
    if selected in {"stg", "prd"}:
        missing = {"DATABASE_URL", "CHECKPOINT_DB_URI"} - merged.keys()
        if missing:
            raise ConfigurationError(
                "Deployment requires explicit " + ", ".join(sorted(missing))
            )

    storage = storage_binding.validate(storage_binding.inputs(merged))
    database = database_binding.validate(database_binding.inputs(merged))
    command = command_binding.validate(command_binding.inputs(merged))
    redis = redis_binding.validate(redis_binding.inputs(merged))
    for binding, instance in (
        (storage_binding, storage),
        (database_binding, database),
        (command_binding, command),
        (redis_binding, redis),
    ):
        for key, value in binding.defaults(instance).items():
            merged.setdefault(key, value)
            sources.setdefault(key, "default")
    api = api_binding.validate(api_binding.inputs(merged))
    # Each consumer owns its fields. Source aliases are shared, never duplicate settings.
    for key, value in api_binding.defaults(api).items():
        merged.setdefault(key, value)
        sources.setdefault(key, "default")
    agent = agent_binding.validate(agent_binding.inputs(merged))
    runtime = runtime_binding.validate(runtime_binding.inputs(merged))
    if "MODEL_CATALOG" in merged and merged["MODEL_CATALOG"] is None:
        raise ConfigurationError("MODEL_CATALOG must be a non-empty mapping")
    if "DEFAULT_MODEL" in merged and not isinstance(
        merged["DEFAULT_MODEL"], str
    ):
        raise ConfigurationError("DEFAULT_MODEL must be an alias")
    from dtest.contracts.model_selection import (
        build_catalog,
        ModelSelectionError,
    )

    try:
        agent = replace(
            agent,
            model_catalog=build_catalog(
                agent, runtime.model_catalog, runtime.default_model
            ),
        )
    except ModelSelectionError:
        raise ConfigurationError(
            "Invalid MODEL_CATALOG or DEFAULT_MODEL"
        ) from None

    sso_input = sso_binding.inputs(merged)
    sso_input.setdefault("namespace", f"dtest-agent:{selected}:sso")
    sso = sso_binding.validate(sso_input)
    if "SSO_NAMESPACE" not in merged:
        sources["SSO_NAMESPACE"] = "derived from APP_ENV"

    worker_input = worker_binding.inputs(merged)
    worker_input.setdefault("database_url", database.database_url)
    worker_input["database_url"] = _postgres_url(
        worker_input["database_url"], "EW_DATABASE_URL"
    )
    worker_input.setdefault("redis_url", redis.redis_url)
    worker_input.setdefault("executor_base_url", agent.executor_base_url)
    if "instance_id" in worker_input:
        prefix = worker_input["instance_id"]
        if not isinstance(prefix, str) or not prefix.strip():
            raise ConfigurationError(
                "Invalid event Worker settings: instance_id"
            )
        worker_input["instance_id"] = f"{prefix}:{uuid4()}"
    worker = worker_binding.validate(worker_input)
    event_enabled = (
        runtime.event_worker_enabled
        if runtime.event_worker_enabled is not None
        else command.agent_worker_enabled
    )
    if "EVENT_WORKER_ENABLED" not in merged:
        sources["EVENT_WORKER_ENABLED"] = "derived from AGENT_WORKER_ENABLED"
    if command.agent_worker_enabled or event_enabled:
        api_url = make_url(
            _postgres_url(database.database_url, "DATABASE_URL")
        )
        event_url = make_url(worker.database_url)

        def target(url):
            return (
                url.username,
                url.password,
                url.host,
                url.port or 5432,
                url.database,
                url.query,
            )

        if target(api_url) != target(event_url):
            raise ConfigurationError(
                "Agent command Worker requires DATABASE_URL and "
                "EW_DATABASE_URL to target the same database; "
                "migrate event data before enabling "
                "it"
            )
    _postgres_url(agent.checkpoint_db_uri, "CHECKPOINT_DB_URI")
    if not worker.redis_url.startswith(("redis://", "rediss://")):
        raise ConfigurationError("Invalid Redis URL: REDIS_URL")
    search = search_binding.validate(search_binding.inputs(merged))
    for binding, instance in (
        (agent_binding, agent),
        (sso_binding, sso),
        (search_binding, search),
        (runtime_binding, runtime),
    ):
        for key, value in binding.defaults(instance).items():
            if value is None:
                continue
            merged.setdefault(key, value)
            sources.setdefault(key, "default")
    # Worker derivations are explicit and never exported as per-process identities.
    for key, origin in {
        "EW_DATABASE_URL": "DATABASE_URL",
        "EW_REDIS_URL": "REDIS_URL",
        "EW_EXECUTOR_BASE_URL": "EXECUTOR_BASE_URL",
    }.items():
        sources.setdefault(key, "derived from " + origin)
    return ServiceSettings(
        api=api,
        agent=agent,
        worker=worker,
        storage=storage,
        database=database,
        commands=command,
        redis=redis,
        sso=sso,
        profile=selected,
        workflow_search=search,
        event_worker_enabled=event_enabled,
        db_init_on_start=runtime.db_init_on_start,
        shutdown_timeout_seconds=runtime.shutdown_timeout_seconds,
        shutdown_drain_seconds=runtime.shutdown_drain_seconds,
        diagnostics_dir=runtime.run_diagnostics_dir,
        diagnostics_stall_seconds=runtime.run_diagnostics_stall_seconds,
        sources=MappingProxyType(sources),
        inputs=MappingProxyType(merged),
        unused_config_keys=tuple(sorted(unused_config_keys)),
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
            raise RuntimeError(
                "Settings already initialized; restart to change configuration"
            )
        _snapshot = settings
        return settings
