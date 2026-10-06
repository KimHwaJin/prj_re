from datetime import datetime
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from dtest.contracts.resources.session_activity_schema import SessionActiveRun, SessionAvailability


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class PageInfo(BaseModel):
    next_cursor: str | None = None
    has_next: bool


T = TypeVar("T")


class Page(APIModel, Generic[T]):
    items: list[T]
    page: PageInfo


class SessionResource(APIModel):
    id: UUID = Field(validation_alias="session_id")
    project_id: UUID
    name: str = Field(validation_alias="session_name")
    current_leaf_message_id: UUID | None
    active_run: SessionActiveRun | None = Field(description="Current public Run, or null when none is identifiable; not Executor execution state.")
    availability: SessionAvailability = Field(description="Snapshot of allowed conversation input; POST rechecks under the session admission lock.")
    settings: dict = Field(description="New sessions store the resolved kernel_profile here. Legacy JSON is returned unchanged; session settings cannot be patched.")
    created_at: datetime
    updated_at: datetime


class MessageResource(APIModel):
    id: UUID = Field(validation_alias="message_id")
    session_id: UUID
    message_type: str
    content: list[dict]
    content_text: str
    message_status: str
    # Chat UI가 일반 assistant/system/tool과 Graph agent 이름을 구분하는 표시 정보입니다.
    metadata: dict = Field(validation_alias="metadata_json")
    sequence_no: int
    created_at: datetime
    updated_at: datetime

