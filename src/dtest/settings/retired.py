"""Reject retired service controls instead of silently pretending they still work."""

from dtest.settings.sources import ConfigurationError

# Migration errors for removed OUR controls. Platform keys never enter this table.
REMOVED_SETTINGS = frozenset(
    {
        "EW_COMMAND_STREAM_NAME",
        "EW_COMMAND_GROUP_NAME",
        "EW_DISPATCH_CONCURRENCY",
        "EW_PUBLISH_LEASE_SECONDS",
    }
)
REMOVED_INFRASTRUCTURE_SETTINGS = frozenset(
    {
        "JUPYTER_ALLOWED_HOSTS",
        "JUPYTER_HEALTH_TIMEOUT_SECONDS",
        "JUPYTER_TOKEN_ENCRYPTION_KEY",
        "REDIS_PING_TIMEOUT_SECONDS",
        "REDIS_HOST",
    }
)


REMOVED_EXECUTOR_PATH_SETTINGS = frozenset(
    {
        "EXECUTOR_EXECUTIONS_PATH",
        "EXECUTOR_EXECUTION_PATH",
        "EXECUTOR_OPERATIONS_PATH",
        "EXECUTOR_RESULT_PATH",
        "EXECUTOR_NOTEBOOK_PATH",
        "EXECUTOR_FINALIZE_PATH",
        "EXECUTOR_CANCEL_PATH",
        "EXECUTOR_ARTIFACTS_PATH",
        "EXECUTOR_EVENTS_PATH",
        "EXECUTOR_JOBS_PATH",
        "EW_EXECUTOR_EVENTS_PATH",
    }
)


def check_retired(values):
    for name in values:
        key = name.upper()
        if key in REMOVED_EXECUTOR_PATH_SETTINGS:
            raise ConfigurationError(
                f"Removed Executor API path setting: {key}; delete it and use EXECUTOR_BASE_URL as the service root"
            )
        if key in REMOVED_SETTINGS:
            raise ConfigurationError(
                f"Removed service setting: {key}; graph concurrency is AGENT_WORKER_CONCURRENCY"
            )
        if key in REMOVED_INFRASTRUCTURE_SETTINGS:
            raise ConfigurationError(
                f"Removed infrastructure API setting: {key}; Jupyter execution uses Executor and Redis uses REDIS_URL"
            )
        if key == "AGENT_HISTORY_MESSAGE_LIMIT":
            raise ConfigurationError(
                "Removed message-count history setting: "
                "AGENT_HISTORY_MESSAGE_LIMIT; use SET_MAX_HISTORY "
                "(turns)"
            )
        if key in {
            "AGENT_PROJECT_MEMORY_MAX_TOPICS",
            "AGENT_PROJECT_MEMORY_TOPIC_MAX_CHARS",
        }:
            raise ConfigurationError(
                f"Removed topic memory setting: {key}; use the single project memory document"
            )
