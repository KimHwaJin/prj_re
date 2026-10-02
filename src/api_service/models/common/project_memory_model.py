"""Bounded topic records and durable idempotency receipts in the service DB."""
from datetime import datetime
from uuid import UUID
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from api_service.models.model_base import Base

class ProjectMemoryModel(Base):
    __tablename__ = 'project_memories'
    project_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey('projects.project_id', ondelete='CASCADE'), primary_key=True)
    section: Mapped[str] = mapped_column(String(32), primary_key=True)
    key: Mapped[str] = mapped_column(String(48), primary_key=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[dict] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

class ProjectMemoryReceiptModel(Base):
    __tablename__ = 'project_memory_receipts'
    project_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey('projects.project_id', ondelete='CASCADE'), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
