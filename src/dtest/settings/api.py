import math

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


class ApiSettings(BaseModel):
    """API values; source selection belongs exclusively to service_settings."""

    app_name: str = "Chat CRUD API"
    api_v1_prefix: str = "/api/v1"
    server_host: str = "127.0.0.1"
    server_port: int = Field(default=8000, validation_alias=AliasChoices("SERVER_PORT", "PORT"))
    # Agent 산출물 생성 중 개발 reloader가 Worker를 죽이지 않도록 기본은 단일 프로세스입니다.
    server_reload: bool = False

    # SSE: fast disconnect checks, commit notifications, slow reconciliation.
    sse_poll_interval_seconds: float = 0.5
    sse_reconcile_interval_seconds: float = 15.0
    sse_max_connections: int = 1000
    sse_heartbeat_seconds: float = 15.0
    sse_event_batch_size: int = 100
    llm_token_flush_interval_seconds: float = 0.2
    llm_token_flush_characters: int = Field(default=256, ge=1)
    # Per Run: queued + batched + writing payload, not process RSS.
    llm_token_buffer_max_bytes: int = Field(default=262144, ge=4)
    llm_token_buffer_max_items: int = Field(default=1024, ge=1)
    llm_token_enqueue_timeout_seconds: float = 5.0
    llm_token_write_timeout_seconds: float = 5.0
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_runtime_values(self):
        if not 1 <= self.server_port <= 65535:
            raise ValueError("server_port must be between 1 and 65535")
        for name in (
            "sse_poll_interval_seconds", "sse_reconcile_interval_seconds", "sse_heartbeat_seconds",
            "llm_token_flush_interval_seconds", "llm_token_enqueue_timeout_seconds", "llm_token_write_timeout_seconds",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if self.sse_max_connections < 1:
            raise ValueError("sse_max_connections must be positive")
        if self.sse_event_batch_size < 1:
            raise ValueError("sse_event_batch_size must be positive")
        return self


class _SettingsProxy:
    """Read the configured API snapshot lazily; imports create no resources."""

    def __getattr__(self, name):
        from dtest.settings.loader import get_settings
        return getattr(get_settings().api, name)


settings = _SettingsProxy()
