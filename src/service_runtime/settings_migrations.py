"""Reject retired service controls instead of silently pretending they still work."""
from service_runtime.settings_sources import ConfigurationError

# Migration errors for removed OUR controls. Platform keys never enter this table.
REMOVED_SETTINGS = frozenset({
    "EW_COMMAND_STREAM_NAME", "EW_COMMAND_GROUP_NAME",
    "EW_DISPATCH_CONCURRENCY", "EW_PUBLISH_LEASE_SECONDS",
})
REMOVED_INFRASTRUCTURE_SETTINGS = frozenset({
    "JUPYTER_ALLOWED_HOSTS", "JUPYTER_HEALTH_TIMEOUT_SECONDS",
    "JUPYTER_TOKEN_ENCRYPTION_KEY", "REDIS_PING_TIMEOUT_SECONDS", "REDIS_HOST",
})


def check_retired(values):
    for name in values:
        key = name.upper()
        if key in REMOVED_SETTINGS:
            raise ConfigurationError(f"Removed service setting: {key}; graph concurrency is AGENT_WORKER_CONCURRENCY")
        if key in REMOVED_INFRASTRUCTURE_SETTINGS:
            raise ConfigurationError(f"Removed infrastructure API setting: {key}; Jupyter execution uses Executor and Redis uses REDIS_URL")
        if key == "AGENT_HISTORY_MESSAGE_LIMIT":
            raise ConfigurationError("Removed message-count history setting: AGENT_HISTORY_MESSAGE_LIMIT; use SET_MAX_HISTORY (turns)")
        if key in {"AGENT_PROJECT_MEMORY_MAX_TOPICS", "AGENT_PROJECT_MEMORY_TOPIC_MAX_CHARS"}:
            raise ConfigurationError(f"Removed topic memory setting: {key}; use the single project memory document")
