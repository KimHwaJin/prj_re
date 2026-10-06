from datetime import datetime
from uuid import UUID
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from dtest.contracts.enums import DeleteYN, UserRole
from dtest.contracts.identity import normalize_user_id
from dtest.contracts.resources.schema_base import ORMModel
from dtest.contracts.values import normalize_name


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str
    user_name: str = Field(min_length=1, max_length=100)
    role: UserRole = UserRole.USER

    _identity = field_validator("user_id")(normalize_user_id)

    @field_validator("user_name")
    @classmethod
    def valid_name(cls, value):
        name = normalize_name(value)
        if not name:
            raise ValueError("user_name cannot be blank")
        return name


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_name: str | None = Field(default=None, min_length=1, max_length=100)
    role: UserRole | None = None

    @model_validator(mode="after")
    def valid_patch(self):
        if not self.model_fields_set or any(
            getattr(self, key) is None for key in self.model_fields_set
        ):
            raise ValueError("Provide a non-null user_name or role")
        if self.user_name is not None:
            self.user_name = UserCreate.valid_name(self.user_name)
        return self


UserListStatus = Literal["active", "deleted", "all"]


class UserSummary(BaseModel):
    """관리자 목록용 사용자 요약. 프로젝트·로그인 세션 정보는 포함하지 않습니다."""

    user_id: str = Field(
        description="서비스의 공개 문자열 사용자 ID. 내부 UUID가 아닙니다."
    )
    user_name: str = Field(description="사용자 표시 이름. 중복될 수 있습니다.")
    role: UserRole = Field(description="서비스 권한 admin 또는 user.")
    is_active: bool = Field(
        description=(
            "현재 soft delete되지 않은 계정인지. 복구나 로그인 세션의 "
            "유효성을 뜻하지 "
            "않습니다."
        )
    )
    created_at: datetime = Field(description="사용자 등록 시각.")
    updated_at: datetime = Field(description="사용자 레코드 최종 변경 시각.")
    deleted_at: datetime | None = Field(
        description="soft delete 시각. 활성 사용자이면 null입니다."
    )


class UserRead(ORMModel):
    user_id: str = Field(
        validation_alias="public_user_id",
        description="서비스의 공개 문자열 사용자 ID. 내부 UUID가 아닙니다.",
    )
    user_name: str
    role: UserRole
    default_project_id: UUID | None = Field(
        default=None,
        description=(
            "활성 기본 프로젝트 UUID. 삭제 등으로 활성 기본 프로젝트가 "
            "없으면 null이며 조회로 새로 만들지 "
            "않습니다."
        ),
    )
    delete_yn: DeleteYN = Field(
        description=(
            "N은 활성, Y는 soft delete된 계정입니다. 관리자 상세는 삭제된 "
            "사용자도 읽을 수 "
            "있습니다."
        )
    )
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class UserMe(UserRead):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
    csrf_token: str
    login_expires_at: int
