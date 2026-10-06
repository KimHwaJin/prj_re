from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dtest.contracts.enums import AgentRunStatus
from dtest.contracts.resources.schema_base import ORMModel


class RunInput(BaseModel):
    messages: list[dict[str, Any]] = Field(min_length=1)


class RunCreate(BaseModel):
    main_model_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[a-zA-Z0-9_.-]+$",
    )
    input: RunInput | None = None
    # 대부분의 HITL은 JSON 객체지만 로컬 mock 데이터 선택은 "mock" 문자열을 사용합니다.
    command: dict[str, Any] | str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    multitask_strategy: Literal["reject"] = "reject"
    stream_mode: list[Literal["messages", "updates", "values", "custom"]] = (
        Field(default_factory=list)
    )
    stream_resumable: bool = True
    on_disconnect: Literal["continue"] = "continue"

    @model_validator(mode="after")
    def input_xor_command(self):
        if (self.input is None) == (self.command is None):
            raise ValueError("Exactly one of input or command is required.")
        return self


class RunResource(ORMModel):
    id: UUID = Field(validation_alias="run_id")
    # resume 요청으로 새 AgentRun row가 생겨도 실제 LangGraph thread는 최초 Run을 사용합니다.
    checkpoint_run_id: UUID | None = None
    task_id: UUID | None = None
    session_id: UUID
    status: AgentRunStatus
    # services/agent_graph_service의 interrupt_payload()와 동일하게 복수 interrupt 배열을 노출합니다.
    interrupt: list[dict[str, Any]] | None
    failure: dict[str, Any] | None
    attempt_count: int
    next_attempt_at: datetime | None
    cancel_reason: str | None
    cancel_requested_at: datetime | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class RunCancel(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)


class AgentRunLogResource(ORMModel):
    """Agent 실행을 조사하는 구조화된 진단 기록. 프론트 진행 프로토콜은 SSE입니다."""

    log_id: UUID = Field(
        description=(
            "개별 저장 로그 UUID. SSE sequence나 resume_token과 무관합니다."
        )
    )
    run_id: UUID = Field(description="HITL 재개 전후에 유지되는 공개 Run ID.")
    event_key: str = Field(
        description=(
            "내부 invocation 내 중복 저장 방지 키. 공개 Run 전체에서 "
            "유일하지 않을 수 "
            "있습니다."
        )
    )
    agent_name: str | None = Field(
        description=(
            "기록을 남긴 Agent 이름. Agent가 특정되지 않은 기록은 null입니다."
        )
    )
    node: str = Field(description="기록을 남긴 그래프 노드 또는 실행 위치.")
    event: str = Field(description="기록 생산자가 부여한 이벤트 이름.")
    kind: str = Field(
        description="로그 분류 문자열. HITL interaction kind와 별개입니다."
    )
    payload: dict[str, Any] = Field(
        description=(
            "생산자가 저장한 진단 객체. 종류별 형식이 다르며 SSE 응답으로 "
            "해석하지 않습니다."
        )
    )
    created_at: datetime = Field(
        description=(
            "DB 로그 저장 시각. 실제 외부 작업 발생 시각이나 인과 순서를 "
            "보장하지 않습니다."
        )
    )


class RunStart(BaseModel):
    main_model_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[a-zA-Z0-9_.-]+$",
    )
    model_config = ConfigDict(extra="forbid")
    input: RunInput
    metadata: dict[str, Any] = Field(default_factory=dict)
    multitask_strategy: Literal["reject"] = "reject"
    stream_mode: list[Literal["messages", "updates", "values", "custom"]] = (
        Field(default_factory=list)
    )
    stream_resumable: bool = True
    on_disconnect: Literal["continue"] = "continue"


class RunResume(BaseModel):
    """Echo the current waiting_input token; a stale screen must not resume a later wait."""

    model_config = ConfigDict(extra="forbid")
    command: dict[str, Any] | str
    resume_token: UUID


PublicRunStatus = Literal[
    "pending",
    "running",
    "waiting_input",
    "waiting_executor",
    "success",
    "error",
    "timeout",
    "canceled",
    "recovery_required",
]


class PublicRunSummary(BaseModel):
    """List entry only; fetch the Run detail for results or HITL actions."""

    run_id: UUID = Field(
        description=(
            "Stable public Run ID across resumes; use it for detail GET/SSE."
        )
    )
    session_id: UUID
    status: PublicRunStatus
    main_model_name: str | None = None
    model_revision: str | None = None
    recovery_required: bool = False
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class PublicRunResource(BaseModel):
    main_model_name: str | None = None
    model_revision: str | None = None
    run_id: UUID = Field(
        description=(
            "Stable public Run ID across HITL resumes and Executor completion."
        )
    )
    session_id: UUID
    status: PublicRunStatus
    resume_token: UUID | None = None
    interrupt: list[dict[str, Any]] | None = None
    failure: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    recovery_required: bool = False
    # Compatibility diagnostics only: clients no longer need these to operate Runs.
    checkpoint_run_id: UUID | None = None
    task_id: UUID | None = None
    attempt_count: int
    next_attempt_at: datetime | None = None
    cancel_reason: str | None = None
    cancel_requested_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
