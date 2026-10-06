"""Internal graph calls; public Run state and external execution are separate."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Identity, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from dtest.infrastructure.database.models.model_base import Base


class AgentCommandModel(Base):
    __tablename__ = "agent_commands"

    namespace: Mapped[str] = mapped_column(Text, primary_key=True)
    command_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    session_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("sessions.session_id", ondelete="CASCADE"), nullable=False)
    ordinal: Mapped[int] = mapped_column(BigInteger, Identity(), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    invocation_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("agent_runs.run_id", ondelete="CASCADE"))
    payload: Mapped[dict | None] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="READY")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    failure_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    owner_token: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    __table_args__ = (
        CheckConstraint("kind IN ('user_start','user_resume','executor_resume')", name="agent_commands_kind"),
        CheckConstraint("state IN ('READY','RUNNING','DONE','IGNORED','FAILED','RECOVERY')", name="agent_commands_state"),
        CheckConstraint("(kind='executor_resume' AND invocation_id IS NULL AND payload IS NOT NULL) OR (kind IN ('user_start','user_resume') AND invocation_id IS NOT NULL AND payload IS NULL)", name="agent_commands_input"),
        Index("agent_commands_ready", "namespace", "available_at", "ordinal", postgresql_where=text("state='READY'")),
        Index("agent_commands_session_order", "session_id", "ordinal", postgresql_where=text("state NOT IN ('DONE','IGNORED','FAILED')")),
    )
