from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from service_contracts.session_settings import SessionSettings

class SessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_name: str | None = Field(default=None, max_length=300)
    settings: SessionSettings = Field(default_factory=SessionSettings,
        description="Session-scoped kernel selection; resolved once at creation. Run model/execution options are not accepted here.")


class SessionUpdate(BaseModel):
    """Rename only; the owning project is fixed when a session is created."""
    model_config = ConfigDict(extra="forbid")

    session_name: str | None = Field(default=None, min_length=1, max_length=300)


class SessionDeleteResult(BaseModel):
    session_id: UUID
    deleted_message_count: int
    detail: str

