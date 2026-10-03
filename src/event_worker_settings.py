from __future__ import annotations

from uuid import uuid4
from string import Formatter

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Settings(BaseModel):
    """Event Worker values, populated by the central service settings loader."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    # API와 동일한 Inbox·명령 원장·실행 연결 DB. 별도 체크포인트 DB와 구분한다.
    database_url: str

    # Executor 원본 이벤트를 수신할 Redis URL이다. 내부 graph 명령은 DB에 저장한다.
    redis_url: str

    # DB 행, Redis key와 기본 Stream 이름을 서비스별로 구분하는 값이다.
    namespace: str = Field(default="dtest-agent", min_length=1)

    # 이벤트 순번 누락 시 실행 이력을 조회할 Executor REST API 주소다.
    executor_base_url: str = "http://localhost:8000/api/v1"

    # 중앙 로더는 EXECUTOR_EXECUTION_PATH + /events에서 유도한다.
    # EXECUTOR_EVENTS_PATH로 별도 경로를 지정할 수 있다. standalone 기본은
    # 위 API-prefixed base에 결합할 상대 경로이며 앞의 /도 base prefix를 유지한다.
    executor_events_path: str = "/executions/{execution_id}/events"

    # Executor가 원본 실행 이벤트를 발행하는 Redis Stream 이름이다.
    executor_event_stream: str = "executor.events"

    # 원본 Executor event Stream을 읽는 consumer group 이름이다.
    # 지정하지 않으면 ``{namespace}:ingress``를 사용한다.
    event_group_name: str | None = None

    # Redis consumer를 구분하는 Worker replica 식별자다.
    # 지정하지 않으면 프로세스를 시작할 때 UUID를 생성한다.
    instance_id: str = Field(default_factory=lambda: str(uuid4()))

    # Executor 원본 이벤트 수신·routing 병렬성. graph 총한도와 별개다.
    ingress_concurrency: int = Field(default=4, ge=1)

    # Worker가 사용하는 PostgreSQL 비동기 연결 풀의 최대 크기다.
    pool_size: int = Field(default=8, ge=2)

    # 이력 보충과 Inbox routing을 한 번에 처리할 최대 개수다.
    batch_size: int = Field(default=100, ge=1, le=500)

    # Router에 처리할 데이터가 있을 때의 반복 간격(초)이다.
    poll_seconds: float = Field(default=0.2, gt=0)

    # 처리할 데이터가 없을 때 지수 backoff가 증가할 최대 간격(초)이다.
    idle_poll_seconds: float = Field(default=2, gt=0)

    # 다른 consumer가 멈춘 pending 메시지를 회수하기 전 대기시간(ms)이다.
    claim_idle_milliseconds: int = Field(default=30000, ge=2001)

    # Redis 원본 이벤트 메시지의 처리 lease가 만료되는 시간(초)이다.
    lease_ttl_seconds: int = Field(default=60, ge=3)

    # 처리 중인 Redis 메시지 lease를 갱신하는 간격(초)이다.
    lease_renew_seconds: int = Field(default=1, ge=1)

    # Executor 명령의 업무 오류를 FAILED로 기록하기 전 최대 시도 횟수다.
    max_handler_attempts: int = Field(default=5, ge=1)

    # 종료 신호 후 실행 중인 consumer와 handler를 기다릴 최대 시간(초)이다.
    shutdown_seconds: float = Field(default=25, ge=0)

    # Executor HTTP 요청과 Redis 연결에 적용하는 timeout(초)이다.
    request_timeout_seconds: float = Field(default=10, gt=0)

    # 선택적 진단 HTTP 포트다. 기본 0: 내장 Worker는 서비스 probe를 사용한다.
    # standalone 진단에서만 8011 등을 명시한다.
    health_port: int = Field(default=0, ge=0, le=65535)

    @field_validator("executor_events_path")
    @classmethod
    def validate_events_path(cls, value):
        try:
            parts = list(Formatter().parse(value))
            fields = [name for _, name, _, _ in parts if name is not None]
            valid = (
                fields == ["execution_id"]
                and all(not spec and not conversion for _, _, spec, conversion in parts)
                and value.startswith("/") and not value.startswith("//")
                and not any(char in value for char in ("?", "#"))
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("executor_events_path must be a relative path with one {execution_id}")
        return value

    @model_validator(mode="after")
    def validate_intervals(self):
        if self.lease_renew_seconds >= self.lease_ttl_seconds:
            raise ValueError("lease_renew_seconds must be less than lease_ttl_seconds")
        if self.idle_poll_seconds < self.poll_seconds:
            raise ValueError("idle_poll_seconds must be at least poll_seconds")
        return self

    @property
    def event_group(self) -> str:
        """실제로 사용할 Executor event consumer group을 반환한다."""

        return self.event_group_name or f"{self.namespace}:ingress"
