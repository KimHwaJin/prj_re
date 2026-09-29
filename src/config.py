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
    # run.py에서 사용하는 로컬 Uvicorn 설정입니다.
    server_host: str = "127.0.0.1"
    server_port: int = 8000
    # Agent 산출물 생성 중 개발 reloader가 Worker를 죽이지 않도록 기본은 단일 프로세스입니다.
    server_reload: bool = False

    # Agent가 사용하는 기존 MODEL_* 환경변수를 FastAPI 설정과 같은 값으로 연결합니다.
    llm_provider: str = Field(
        default="openai_compatible",
        validation_alias=AliasChoices("MODEL_PROVIDER", "LLM_PROVIDER"),
    )
    llm_model_name: str = Field(
        # default="qwen38-27b-fp8",
        default="qwen38-27b-nvfp4",
        validation_alias=AliasChoices("MODEL_NAME", "LLM_MODEL_NAME"),
    )
    llm_api_base_url: str = Field(
        default="http://model.frodo.com/v1",
        validation_alias=AliasChoices("API_BASE_URL", "LLM_API_BASE_URL"),
    )
    llm_api_key: str = Field(
        default="dummy",
        validation_alias=AliasChoices("MODEL_API_KEY", "LLM_API_KEY"),
    )
    llm_timeout_seconds: float = Field(
        default=60.0,
        validation_alias=AliasChoices("MODEL_TIMEOUT_SECONDS", "LLM_TIMEOUT_SECONDS"),
    )
    llm_temperature: float = 0.2
    llm_max_output_tokens: int = Field(
        default=8192,
        validation_alias=AliasChoices(
            "MODEL_MAX_OUTPUT_TOKENS", "LLM_MAX_OUTPUT_TOKENS"
        ),
    )
    llm_max_retries: int = Field(
        default=0,
        validation_alias=AliasChoices("MODEL_MAX_RETRIES", "LLM_MAX_RETRIES"),
    )
    # Qwen의 reasoning 출력과 구조화 응답 방식을 환경별로 조절합니다.
    llm_enable_thinking: bool | None = Field(
        default=None,
        validation_alias=AliasChoices("MODEL_ENABLE_THINKING", "LLM_ENABLE_THINKING"),
    )
    llm_structured_output_mode: str = Field(
        default="prompt_json",
        validation_alias=AliasChoices(
            "MODEL_STRUCTURED_OUTPUT_MODE", "LLM_STRUCTURED_OUTPUT_MODE"
        ),
    )
    llm_retry_backoff_seconds: float = 0.5
    # E03: Worker가 heartbeat를 갱신하지 못했을 때 Run을 stale로 판단하는 lease 길이입니다.
    task_lease_seconds: int = 300
    task_reconcile_interval_seconds: int = 30
    # 단일 서버/로컬에서는 API lifespan과 함께 Reconciler를 실행합니다.
    # 운영에서 별도 Reconciler Deployment를 둘 때는 false로 끌 수 있습니다.
    task_reconciler_enabled: bool = True
    # 별도 Pod 없이 서비스 bootstrap lifespan에서 durable DB queue를 소비합니다.
    agent_worker_enabled: bool = True
    agent_worker_poll_interval_seconds: float = 0.25
    # Per process, not per Pod. YAML explicit value takes priority over env.
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
    # LangGraph HITL state를 API 재시작 뒤에도 재개하기 위한 비동기 PostgreSQL checkpoint입니다.
    graph_checkpointer: str = "postgres"
    checkpoint_db_uri: str = (
        "postgresql://postgres:1234@127.0.0.1:5432/chat_app?sslmode=disable"
    )
    # E13: DB에는 이 root 기준 상대 경로만 저장해 서버 이동과 path traversal 방지를 돕습니다.
    workflow_storage_root: Path = Path("var/workflows")
    # E10-T04: comma-separated exact host allowlist. 운영 내부 DNS는 환경변수로 추가합니다.
    jupyter_allowed_hosts: str = "127.0.0.1,localhost"
    jupyter_health_timeout_seconds: float = 5.0
    # Fernet key. Token을 등록할 때 비어 있으면 503으로 거부해 평문 저장을 막습니다.
    jupyter_token_encryption_key: str = ""
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
    # 로컬에서는 외부 Jupyter/executor가 없으므로 제출 직전 성공 응답으로 대체합니다.
    executor_submit_enabled: bool = False
    # executor 비활성 상태에서도 생성 코드 파일은 쓰기 가능한 로컬 경로에 보존합니다.
    executor_shared_input_root: Path = Field(
        default=Path("/workspace/pv"),
        validation_alias="EXECUTOR_SHARED_INPUT_ROOT",
    )
    # Workflow 송수신용 Redis broker. 비밀번호 특수문자는 URL 인코딩이 필요할 수 있습니다.
    redis_host: str = "127.0.0.1:6379"
    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_ping_timeout_seconds: float = 5.0
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
            "agent_worker_poll_interval_seconds", "task_cancel_poll_interval_seconds",
            "sse_poll_interval_seconds", "sse_reconcile_interval_seconds", "sse_heartbeat_seconds", "llm_timeout_seconds",
            "redis_ping_timeout_seconds", "jupyter_health_timeout_seconds",
            "run_cleanup_timeout_seconds", "run_monitor_timeout_seconds",
            "llm_token_flush_interval_seconds", "llm_token_enqueue_timeout_seconds", "llm_token_write_timeout_seconds",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        for name in ("agent_worker_max_retries", "llm_max_retries", "agent_worker_retry_backoff_seconds",
                     "agent_worker_retry_max_backoff_seconds", "llm_retry_backoff_seconds"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.sse_max_connections < 1:
            raise ValueError("sse_max_connections must be positive")
        if self.sse_event_batch_size < 1:
            raise ValueError("sse_event_batch_size must be positive")
        return self


class _SettingsProxy:
    """Compatibility for existing imports without reading files at import time."""

    def __getattr__(self, name):
        from service_settings import get_settings
        return getattr(get_settings().api, name)

settings = _SettingsProxy()
