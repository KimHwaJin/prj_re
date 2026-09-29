from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from api_service.core.enums import DeleteYN, UserRole
from api_service.core.user_identity import normalize_user_id
from api_service.schemas.schema_base import ORMModel
from api_service.services.helpers import normalize_name


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
        if not self.model_fields_set or any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("Provide a non-null user_name or role")
        if self.user_name is not None:
            self.user_name = UserCreate.valid_name(self.user_name)
        return self


class UserRead(ORMModel):
    user_id: str = Field(validation_alias="public_user_id")
    user_name: str
    role: UserRole
    default_project_id: UUID | None = None
    delete_yn: DeleteYN
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
