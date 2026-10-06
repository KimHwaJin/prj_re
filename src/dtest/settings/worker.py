import math

from pydantic import BaseModel, ConfigDict, Field, model_validator


class WorkerSettings(BaseModel):
    """Worker settings owned once by the central snapshot."""

    model_config = ConfigDict(
        populate_by_name=True, extra="forbid", frozen=True, allow_inf_nan=False
    )
    task_lease_seconds: int = 300
    task_reconcile_interval_seconds: int = 30
    task_reconciler_enabled: bool = True
    agent_worker_enabled: bool = True
    agent_worker_poll_interval_seconds: float = 0.25
    agent_worker_notify_enabled: bool = True
    agent_worker_reconcile_interval_seconds: float = 5.0
    agent_worker_concurrency: int = Field(default=1, ge=1)
    agent_worker_max_retries: int = 3
    agent_worker_retry_backoff_seconds: float = 1.0
    agent_worker_retry_max_backoff_seconds: float = 30.0
    task_cancel_poll_interval_seconds: float = 0.25
    run_cleanup_timeout_seconds: float = 5.0
    run_monitor_timeout_seconds: float = 3.0

    @model_validator(mode="after")
    def validate_limits(self):
        for name in (
            "task_lease_seconds",
            "task_reconcile_interval_seconds",
            "agent_worker_poll_interval_seconds",
            "agent_worker_reconcile_interval_seconds",
            "task_cancel_poll_interval_seconds",
            "run_cleanup_timeout_seconds",
            "run_monitor_timeout_seconds",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        for name in (
            "agent_worker_max_retries",
            "agent_worker_retry_backoff_seconds",
            "agent_worker_retry_max_backoff_seconds",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        return self
