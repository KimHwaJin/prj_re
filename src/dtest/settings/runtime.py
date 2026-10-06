"""App lifecycle and optional service adapters, independent of source selection."""

from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field, field_validator


class RuntimeSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    app_env: str = "local"
    event_worker_enabled: bool | None = (
        None  # Omitted: follow AGENT_WORKER_ENABLED.
    )
    shutdown_timeout_seconds: float = Field(default=25, gt=0)
    shutdown_drain_seconds: float = Field(default=20, ge=0)
    run_diagnostics_dir: Path | None = None
    run_diagnostics_stall_seconds: float = Field(default=5, ge=1)
    model_catalog: dict | None = Field(default=None, repr=False)
    default_model: str | None = None

    @field_validator("event_worker_enabled", mode="before")
    @classmethod
    def explicit_worker_switch_is_boolean(cls, value):
        if value is None:
            raise ValueError("Explicit worker switch cannot be null")
        return value
