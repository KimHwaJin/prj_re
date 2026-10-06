from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


def _normalize_tags(tags: list[str] | None) -> list[str] | None:
    if tags is None:
        return None
    normalized = []
    for value in tags:
        tag = value.strip().lower()
        if not tag or len(tag) > 50:
            raise ValueError("tags must contain 1-50 character values")
        if tag not in normalized:
            normalized.append(tag)
    if len(normalized) > 20:
        raise ValueError("at most 20 tags are allowed")
    return normalized


def _queries(values):
    normalized = [value.strip() for value in values]
    if any(not value or len(value) > 4000 for value in normalized):
        raise ValueError(
            "user_queries must contain nonblank strings of at most "
            "4000 characters"
        )
    if len(set(normalized)) != len(normalized):
        raise ValueError("Duplicate user_queries are not allowed")
    if sum(map(len, normalized)) > 256000:
        raise ValueError(
            "user_queries total length must not exceed 256000 characters"
        )
    return normalized


class WorkflowCandidateCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_queries: list[str] = Field(
        min_length=1,
        max_length=1000,
        description=(
            "이 Workflow가 해결할 사용자 질문 예시. 각 항목은 별도로 "
            "임베딩합니다."
        ),
    )
    _queries = field_validator("user_queries")(_queries)
    # Public 2.0 permits direct authoring; a supplied Run remains provenance.
    source_run_id: UUID | None = None
    document: dict[str, Any]
    tags: list[str] = Field(default_factory=list)

    _tags = field_validator("tags")(_normalize_tags)


class WorkflowUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_queries: list[str] | None = Field(
        default=None, min_length=1, max_length=1000
    )
    expected_resource_revision: int | None = Field(
        default=None,
        ge=1,
        description=(
            "GET에서 받은 전체 자산 버전. 쿼리 수정에 필수이며 모든 "
            "변경의 충돌을 검사합니다."
        ),
    )
    _queries = field_validator("user_queries")(
        lambda v: _queries(v) if v is not None else v
    )
    document: dict[str, Any] | None = None
    # Content SHA is an optimistic revision token, not the format version.
    expected_content_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )

    @model_validator(mode="after")
    def revision_required(self):
        if (
            "user_queries" in self.model_fields_set
            and self.user_queries is None
        ):
            raise ValueError("user_queries cannot be null")
        if (
            self.user_queries is not None
            and self.expected_resource_revision is None
        ):
            raise ValueError(
                "query update requires expected_resource_revision"
            )
        if (
            self.document is not None
            and self.expected_content_sha256 is None
            and self.expected_resource_revision is None
        ):
            raise ValueError(
                "document update requires expected_content_sha256"
            )
        return self

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    tags: list[str] | None = None

    _tags = field_validator("tags")(_normalize_tags)


class WorkflowClone(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    tags: list[str] | None = None

    _tags = field_validator("tags")(_normalize_tags)


class WorkflowResource(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    workflow_id: UUID
    name: str
    description: str
    goal: str
    schema_version: str
    lifecycle: Literal["candidate", "template"]
    file_path: str
    content_sha256: str
    user_queries: list[str]
    resource_revision: int
    search_revision: int
    index_state: Literal["not_indexed", "pending", "ready", "failed"]
    index_error: str | None
    # 운영 초기 template처럼 Agent 실행 없이 등록된 자산은 NULL일 수 있습니다.
    source_run_id: UUID | None
    source_workflow_id: UUID | None
    created_by_user_id: UUID | None
    is_recommendable: bool
    tags: list[str]
    document: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
