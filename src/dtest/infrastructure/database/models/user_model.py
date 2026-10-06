from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Enum, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from dtest.contracts.enums import DeleteYN, UserRole, enum_values
from dtest.infrastructure.database.models.model_base import Base, TimestampMixin


class UserModel(TimestampMixin, Base):
    """서비스 사용자."""

    __tablename__ = "users"

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    user_name: Mapped[str] = mapped_column(String(100), nullable=False)
    public_user_id: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", values_callable=enum_values,
             native_enum=False, create_constraint=True),
        nullable=False, default=UserRole.USER, server_default="user",
    )
    __table_args__ = (
        UniqueConstraint("public_user_id", name="uq_users_public_user_id"),
        CheckConstraint("public_user_id ~ '^[a-z0-9][a-z0-9_.@-]{0,99}$' AND public_user_id <> 'me'",
                        name="ck_users_public_user_id"),
    )
    delete_yn: Mapped[DeleteYN] = mapped_column(
        Enum(
            DeleteYN,
            name="delete_yn",
            values_callable=enum_values,
        ),
        nullable=False,
        default=DeleteYN.N,
        server_default=DeleteYN.N.value,
    )

    owned_projects = relationship(
        "ProjectModel",
        back_populates="owner",
        foreign_keys="ProjectModel.user_id",
    )
    sessions = relationship(
        "SessionModel",
        back_populates="user",
        foreign_keys="SessionModel.user_id",
    )
