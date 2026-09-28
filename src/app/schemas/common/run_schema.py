from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.core.enums import AgentRunStatus
from app.schemas.schema_base import ORMModel


class RunInput(BaseModel):
    messages: list[dict[str, Any]] = Field(min_length=1)


class RunCreate(BaseModel):
    input: RunInput | None = None
    # 대부분의 HITL은 JSON 객체지만 로컬 mock 데이터 선택은 "mock" 문자열을 사용합니다.
    command: dict[str, Any] | str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    multitask_strategy: Literal["reject"] = "reject"
    stream_mode: list[Literal["messages", "updates", "values", "custom"]] = Field(default_factory=list)
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
    # DB/RunService의 interrupt_payload()와 동일하게 복수 interrupt 배열을 노출합니다.
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
    """프런트 진행 화면에서 표시할 Agent 실행 로그입니다."""

    log_id: UUID
    run_id: UUID
    event_key: str
    agent_name: str | None
    node: str
    event: str
    kind: str
    payload: dict[str, Any]
    created_at: datetime

