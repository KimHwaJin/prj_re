from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

class SessionCreate(BaseModel):
    session_name: str | None = Field(default=None, max_length=300)
    settings: dict[str, Any] = Field(default_factory=dict)


class SessionUpdate(BaseModel):
    session_name: str | None = Field(default=None, min_length=1, max_length=300)

    # 현재 project_id와 구분하기 위해 이동 대상은 별도 이름을 사용합니다.
    target_project_id: UUID | None = None


class SessionDeleteResult(BaseModel):
    session_id: UUID
    deleted_message_count: int
    detail: str

