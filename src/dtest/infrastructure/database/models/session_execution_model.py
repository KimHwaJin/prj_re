"""Persistent graph ownership: expiry never authorizes a second writer."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from dtest.infrastructure.database.models.model_base import Base


class SessionExecutionModel(Base):
    __tablename__ = "session_executions"

    # No FK: independently hosted Agent sessions also participate in exclusion.
    session_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    token: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    owner_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    owner_kind: Mapped[str | None] = mapped_column(String(20))
    owner_process: Mapped[str | None] = mapped_column(String(255))
    acquired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recovery_required: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    recovery_reason: Mapped[str | None] = mapped_column(Text)
