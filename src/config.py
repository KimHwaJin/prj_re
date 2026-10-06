from pathlib import Path
import math

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


class Settings(BaseModel):
    """API values; source selection belongs exclusively to service_settings."""

    app_name: str = "Chat CRUD API"
    api_v1_prefix: str = "/api/v1"
    # 로컬 기본값입니다. 배포 환경에서는 DATABASE_URL로 덮어쓰세요.
    database_url: str = (
        "postgresql+asyncpg://postgres:1234@127.0.0.1:5432/chat_app"
    )
    sql_echo: bool = False
    database_pool_size: int = 10
    database_max_overflow: int = 10
    database_pool_timeout_seconds: float = 30.0
    database_pool_recycle_seconds: int = 300
    # Per physical asyncpg connection. 0 preserves the diagnostic legacy behavior.
    # Direct PostgreSQL deployments can opt into a bounded prepared-statement cache.
    database_prepared_statement_cache_size: int = Field(default=0, ge=0, le=1000)
    # run.py에서 사용하는 로컬 Uvicorn 설정입니다.
    server_host: str = "127.0.0.1"
    server_port: int = Field(default=8000, validation_alias=AliasChoices("SERVER_PORT", "PORT"))
    # Agent 산출물 생성 중 개발 reloader가 Worker를 죽이지 않도록 기본은 단일 프로세스입니다.
    server_reload: bool = False

    # E03: Worker가 heartbeat를 갱신하지 못했을 때 Run을 stale로 판단하는 lease 길이입니다.
    task_lease_seconds: int = 300
    task_reconcile_interval_seconds: int = 30
    # 같은 서비스 lifespan에서 세션 잠금/Run lease 정합성을 점검합니다.
    # 실행기를 비활성화하는 진단 환경에서는 함께 끌 수 있습니다.
    task_reconciler_enabled: bool = True
    # 별도 Pod 없이 서비스 bootstrap lifespan에서 durable DB queue를 소비합니다.
    agent_worker_enabled: bool = True
    # LISTEN 미연결/비활성 시 fallback 조회 간격. 정상 연결의 유휴 조회와 별개다.
    agent_worker_poll_interval_seconds: float = 0.25
    # Worker와 SSE가 프로세스당 하나의 알림 연결을 공유한다.
    agent_worker_notify_enabled: bool = True
    # 정상 LISTEN 중 신호 유실을 보완하는 최대 유휴 확인 간격(초).
    agent_worker_reconcile_interval_seconds: float = 5.0
    # Per process, not per Pod. YAML explicit value takes priority over env.
    # Per-process total graph calls: user start/resume + Executor resume share this limit.
    agent_worker_concurrency: int = Field(default=1, ge=1)
    # 최초 실행은 제외한 자동 재시도 횟수입니다. 3이면 총 최대 4회 실행합니다.
    agent_worker_max_retries: int = 3
    # 재시도 폭주를 막기 위한 지수 backoff의 시작/최대 대기 시간입니다.
    agent_worker_retry_backoff_seconds: float = 1.0
    agent_worker_retry_max_backoff_seconds: float = 30.0
    # 실행 중 취소 요청을 다른 API worker에서도 감지하기 위한 DB polling 주기입니다.
    task_cancel_poll_interval_seconds: float = 0.25
    # 정상 stop을 기다릴 시간 및 취소 후 종료를 관측할 시간을 각각 적용합니다.
    run_cleanup_timeout_seconds: float = 5.0
    run_monitor_timeout_seconds: float = 3.0
    graph_checkpointer: str = "postgres"
    # E13: DB에는 이 root 기준 상대 경로만 저장해 서버 이동과 path traversal 방지를 돕습니다.
    workflow_storage_root: Path = Path("var/workflows")
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
    # SSO 로그인 세션과 Executor Streams의 공통 주소. 용도별 연결풀은 분리합니다.
    redis_url: str = Field(default="redis://127.0.0.1:6379/0", validation_alias=AliasChoices("REDIS_URL", "EW_REDIS_URL"))
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_runtime_values(self):
        if not 1 <= self.server_port <= 65535:
            raise ValueError("server_port must be between 1 and 65535")
        if self.database_pool_size < 1 or self.database_max_overflow < 0:
            raise ValueError("database pool sizes must be bounded and non-negative")
        if self.graph_checkpointer not in {"memory", "postgres"}:
            raise ValueError("graph_checkpointer must be memory or postgres")
        for name in (
            "database_pool_timeout_seconds", "task_lease_seconds", "task_reconcile_interval_seconds",
            "agent_worker_poll_interval_seconds", "agent_worker_reconcile_interval_seconds", "task_cancel_poll_interval_seconds",
            "sse_poll_interval_seconds", "sse_reconcile_interval_seconds", "sse_heartbeat_seconds",
            "run_cleanup_timeout_seconds", "run_monitor_timeout_seconds",
            "llm_token_flush_interval_seconds", "llm_token_enqueue_timeout_seconds", "llm_token_write_timeout_seconds",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        for name in ("agent_worker_max_retries", "agent_worker_retry_backoff_seconds",
                     "agent_worker_retry_max_backoff_seconds"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.sse_max_connections < 1:
            raise ValueError("sse_max_connections must be positive")
        if self.sse_event_batch_size < 1:
            raise ValueError("sse_event_batch_size must be positive")
        return self


class _SettingsProxy:
    """Read the configured API snapshot lazily; imports create no resources."""

    def __getattr__(self, name):
        from service_settings import get_settings
        return getattr(get_settings().api, name)


settings = _SettingsProxy()
