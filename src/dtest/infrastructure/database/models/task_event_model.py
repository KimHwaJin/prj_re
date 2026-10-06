from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from dtest.infrastructure.database.models.model_base import Base


class TaskEventModel(Base):
    """E05-T05 재생 가능한 SSE 원본. Redis/PubSub 없이 PostgreSQL에 보존합니다."""

    __tablename__ = "task_events"

    task_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    task_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tasks.task_id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agent_runs.run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_run_log_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agent_run_logs.log_id", ondelete="CASCADE"),
        nullable=True,
    )
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True
    )
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    task = relationship("TaskModel", back_populates="events")

    __table_args__ = (
        UniqueConstraint(
            "agent_run_log_id", name="uq_task_events_agent_run_log"
        ),
        UniqueConstraint(
            "task_id", "sequence", name="uq_task_events_task_sequence"
        ),
        Index("ix_task_events_task_sequence", "task_id", "sequence"),
    )
