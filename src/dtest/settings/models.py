"""Immutable runtime snapshot and safe startup/check-config diagnostics."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dtest.settings.agent import AgentSettings
from dtest.settings.api import ApiSettings as APISettings
from dtest.settings.auth import SsoSettings
from dtest.settings.database import DatabaseSettings
from dtest.settings.events import EventWorkerSettings
from dtest.settings.redis import RedisSettings
from dtest.settings.search import WorkflowSearchSettings
from dtest.settings.storage import StorageSettings
from dtest.settings.worker import WorkerSettings as CommandSettings


@dataclass(frozen=True, repr=False)
class ServiceSettings:
    storage: StorageSettings
    database: DatabaseSettings
    commands: CommandSettings
    redis: RedisSettings
    api: APISettings
    agent: AgentSettings
    worker: EventWorkerSettings
    sso: SsoSettings
    profile: str
    event_worker_enabled: bool
    shutdown_timeout_seconds: float
    shutdown_drain_seconds: float
    diagnostics_dir: Path | None
    diagnostics_stall_seconds: float
    sources: Mapping[str, str]
    inputs: Mapping[str, Any] = field(
        repr=False
    )  # Private file export only; never add to summary/API.
    workflow_search: WorkflowSearchSettings = field(
        default_factory=WorkflowSearchSettings
    )
    unused_config_keys: tuple[str, ...] = ()
    db_init_on_start: bool = False

    def summary(self) -> dict[str, Any]:
        """Safe for startup logs / --check-config; never dump values or DSNs."""
        return {
            "profile": self.profile,
            "db_init_on_start": self.db_init_on_start,
            "server_host": self.api.server_host,
            "server_port": self.api.server_port,
            "server_processes": 1,
            "task_reconciler_enabled": self.commands.task_reconciler_enabled,
            "event_ingress_concurrency": self.worker.ingress_concurrency,
            "event_graph_dispatchers": 0,
            "agent_command_concurrency": self.commands.agent_worker_concurrency,
            "agent_worker_notify_enabled": self.commands.agent_worker_notify_enabled,
            "agent_worker_reconcile_interval_seconds": self.commands.agent_worker_reconcile_interval_seconds,
            "agent_worker_poll_interval_seconds": self.commands.agent_worker_poll_interval_seconds,
            "event_health_port": self.worker.health_port,
            # Configured maxima, not currently checked-out connections. Distinct
            # drivers/lifetimes require distinct pools, even with one endpoint.
            "connection_pool_limits": {
                "crud": self.database.database_pool_size
                + self.database.database_max_overflow,
                "checkpoint": self.agent.checkpoint_pool_max_size,
                "submission_bridge": self.worker.pool_size,
                "event": self.worker.pool_size,
                "project_memory": min(2, self.database.database_pool_size),
                "notification_listener": 1,  # Shared by Worker and SSE, outside pools.
            },
            "agent_worker_enabled": self.commands.agent_worker_enabled,
            "agent_worker_concurrency": self.commands.agent_worker_concurrency,
            "recursion_limit": self.agent.recursion_limit,
            "active_multi_turn": self.agent.active_multi_turn,
            "history_previous_turns": self.agent.set_max_history,
            "active_trace": self.agent.active_trace,
            "event_worker_enabled": self.event_worker_enabled,
            "shutdown_drain_seconds": self.shutdown_drain_seconds,
            "shutdown_timeout_seconds": self.shutdown_timeout_seconds,
            "identity_mode": "sso_cookie",
            "sso_adapter_configured": bool(self.sso.adapter_factory),
            "settings_sources": dict(self.sources),
            "unused_config_keys": list(self.unused_config_keys),
        }
