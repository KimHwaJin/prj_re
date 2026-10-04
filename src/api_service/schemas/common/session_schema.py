from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

class SessionCreate(BaseModel):
    session_name: str | None = Field(default=None, max_length=300)
    settings: dict[str, Any] = Field(default_factory=dict)


class SessionUpdate(BaseModel):
    """Rename only; the owning project is fixed when a session is created."""
    model_config = ConfigDict(extra="forbid")

    session_name: str | None = Field(default=None, min_length=1, max_length=300)


class SessionDeleteResult(BaseModel):
    session_id: UUID
    deleted_message_count: int
    detail: str

