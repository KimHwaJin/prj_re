from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _normalize_tags(tags: list[str]) -> list[str]:
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


class WorkflowCandidateCreate(BaseModel):
    # 자동 생성 candidate는 반드시 성공 여부를 검증할 원본 Run이 필요합니다.
    source_run_id: UUID
    document: dict[str, Any]
    tags: list[str] = Field(default_factory=list)

    _tags = field_validator("tags")(_normalize_tags)


class WorkflowUpdate(BaseModel):
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

