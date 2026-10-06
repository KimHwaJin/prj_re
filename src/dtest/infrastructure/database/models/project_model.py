from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from dtest.contracts.enums import (
    DeleteYN,
    enum_values,
)
from dtest.infrastructure.database.models.model_base import Base, TimestampMixin


class ProjectModel(TimestampMixin, Base):
    """User 소유 디렉토리이자 하위 Session의 시스템 프롬프트 경계."""

    __tablename__ = "projects"

    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    project_name: Mapped[str] = mapped_column(String(200), nullable=False)

    system_prompt: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
        server_default="",
    )
    prompt_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    delete_yn: Mapped[DeleteYN] = mapped_column(
        Enum(
            DeleteYN,
            name="delete_yn",
            values_callable=enum_values,
            create_type=False,
        ),
        nullable=False,
        default=DeleteYN.N,
        server_default=DeleteYN.N.value,
    )

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "user_id",
            name="uq_projects_project_owner",
        ),
    )

    owner = relationship(
        "UserModel",
        back_populates="owned_projects",
        foreign_keys=[user_id],
    )
    sessions = relationship(
        "SessionModel",
        back_populates="project",
        foreign_keys="SessionModel.project_id",
    )
