"""Project request and read contracts; shared memory is a separate resource."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from api_service.schemas.common.api_schema import APIModel


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Preserve the existing blank-name/default conflict policy in the service.
    project_name: str = Field(max_length=200, description="프로젝트 이름. 공백은 정규화하며 빈 이름은 기본 프로젝트와의 충돌로 409입니다.")
    system_prompt: str = Field(default="", description="프로젝트 공통 지침. 생략 또는 빈 문자열은 지침 없음이며 null은 허용하지 않습니다.")


def _optional_string_schema(schema):
    """Omission keeps a value; explicitly supplied null is not a valid patch."""
    choices = schema.pop("anyOf")
    schema.update(next(choice for choice in choices if choice.get("type") == "string"))
    schema.pop("default", None)


class ProjectUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"minProperties": 1})
    project_name: str | None = Field(default=None, min_length=1, max_length=200, json_schema_extra=_optional_string_schema, description="변경할 이름. 생략은 유지, 명시적 null/공백은 허용하지 않습니다. 기본 프로젝트 이름은 변경할 수 없습니다.")
    system_prompt: str | None = Field(default=None, json_schema_extra=_optional_string_schema, description="변경할 공통 지침. 생략은 유지, 빈 문자열은 초기화, 명시적 null은 허용하지 않습니다.")

    @model_validator(mode="after")
    def require_a_change(self):
        if not self.model_fields_set or any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("Provide a non-null project_name or system_prompt; use an empty string to clear system_prompt.")
        return self


class ProjectSummary(APIModel):
    """Project selection list; no prompt or child collections."""
    id: UUID = Field(validation_alias="project_id", description="프로젝트 UUID. 기존 공개 응답 필드 이름 id를 유지합니다.")
    name: str = Field(validation_alias="project_name", description="프로젝트 표시 이름. 요청 body에서는 project_name을 사용합니다.")
    is_default: bool = Field(description="사용자의 기본 프로젝트인지. 이름 변경·개별 삭제가 제한됩니다.")
    created_at: datetime = Field(description="프로젝트 생성 시각.")
    updated_at: datetime = Field(description="프로젝트 레코드 최종 변경 시각. 하위 세션의 마지막 대화 시각은 아닙니다.")


class ProjectResource(ProjectSummary):
    """Full project detail, also returned by create and update."""
    system_prompt: str = Field(description="프로젝트 공통 지침 전체. 새 Run 실행 시작 시 snapshot으로 고정하며 재개는 기존 값을 유지합니다.")
    prompt_version: int = Field(description="system_prompt의 변경 버전. 생성 시 1이고 내용이 실제로 변경될 때만 증가합니다.")
