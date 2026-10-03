from __future__ import annotations

from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


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

    # Executor가 원본 실행 이벤트를 발행하는 Redis Stream 이름이다.
    executor_event_stream: str = "executor.events"

    # 이전 배포 이행/진단용 필드. 현재 런타임은 내부 Redis Stream을 발행하지 않는다.
    # 지정하지 않으면 ``{namespace}:commands``를 사용한다.
    command_stream_name: str | None = None

    # 원본 Executor event Stream을 읽는 consumer group 이름이다.
    # 지정하지 않으면 ``{namespace}:ingress``를 사용한다.
    event_group_name: str | None = None

    # 이전 배포 이행/진단용 필드. 현재 런타임은 dispatch group에 합류하지 않는다.
    # 지정하지 않으면 ``{namespace}:dispatch``를 사용한다.
    command_group_name: str | None = None

    # Redis consumer를 구분하는 Worker replica 식별자다.
    # 지정하지 않으면 프로세스를 시작할 때 UUID를 생성한다.
    instance_id: str = Field(default_factory=lambda: str(uuid4()))

    # ingress 전용 동시성이 없을 때 사용하는 수신 consumer 기본값이다.
    concurrency: int = Field(default=4, ge=1)

    # Executor 원본 이벤트를 동시에 수집하는 consumer 수다.
    # 지정하지 않으면 ``concurrency`` 값을 사용한다.
    ingress_concurrency: int | None = Field(default=None, ge=1)

    # Deprecated: 현재 graph 총한도는 AGENT_WORKER_CONCURRENCY만 사용한다.
    # 지정하지 않으면 ``concurrency`` 값을 사용한다.
    dispatch_concurrency: int | None = Field(default=None, ge=1)

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

    # 세션 잠금과 메시지 처리 lease가 만료되는 시간(초)이다.
    lease_ttl_seconds: int = Field(default=60, ge=3)

    # 처리 중인 세션 잠금과 메시지 lease를 갱신하는 간격(초)이다.
    lease_renew_seconds: int = Field(default=1, ge=1)

    # Deprecated: 이전 Outbox 진단용. 현재 런타임에서는 사용하지 않는다.
    publish_lease_seconds: int = Field(default=30, ge=1)

    # Executor 명령의 업무 오류를 FAILED로 기록하기 전 최대 시도 횟수다.
    max_handler_attempts: int = Field(default=5, ge=1)

    # 종료 신호 후 실행 중인 consumer와 handler를 기다릴 최대 시간(초)이다.
    shutdown_seconds: float = Field(default=25, ge=0)

    # Executor HTTP 요청과 Redis 연결에 적용하는 timeout(초)이다.
    request_timeout_seconds: float = Field(default=10, gt=0)

    # 선택적 진단 HTTP 포트다. 기본 0: 내장 Worker는 서비스 probe를 사용한다.
    # standalone 진단에서만 8011 등을 명시한다.
    health_port: int = Field(default=0, ge=0, le=65535)

    @model_validator(mode="after")
    def validate_intervals(self):
        if self.lease_renew_seconds >= self.lease_ttl_seconds:
            raise ValueError("lease_renew_seconds must be less than lease_ttl_seconds")
        if self.idle_poll_seconds < self.poll_seconds:
            raise ValueError("idle_poll_seconds must be at least poll_seconds")
        return self

    @property
    def command_stream(self) -> str:
        """실제로 사용할 내부 command Stream 이름을 반환한다."""

        return self.command_stream_name or f"{self.namespace}:commands"

    @property
    def event_group(self) -> str:
        """실제로 사용할 Executor event consumer group을 반환한다."""

        return self.event_group_name or f"{self.namespace}:ingress"

    @property
    def command_group(self) -> str:
        """실제로 사용할 내부 command consumer group을 반환한다."""

        return self.command_group_name or f"{self.namespace}:dispatch"

    @property
    def ingress_workers(self) -> int:
        """원본 Executor event를 소비할 실제 동시성 값을 반환한다."""

        return self.ingress_concurrency or self.concurrency

    @property
    def dispatch_workers(self) -> int:
        """내부 command handler를 실행할 실제 동시성 값을 반환한다."""

        return self.dispatch_concurrency or self.concurrency
