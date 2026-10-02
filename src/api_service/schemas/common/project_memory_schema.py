"""Explicitly shared knowledge can be inspected, corrected and removed."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from service_contracts.project_memory import MemorySection

class MemoryPut(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    content: str = Field(min_length=1, max_length=1000, description='Explicit project-wide knowledge or preference shared by this user.')
    expected_version: int = Field(ge=0, description='0 for a new topic; echo the current version to edit or restore a deleted topic.')

    @field_validator('content')
    @classmethod
    def nonblank(cls,value):
        if not value.strip(): raise ValueError('Memory content cannot be blank')
        return value

class MemorySource(BaseModel):
    kind: Literal['user_edit','user_request'] = Field(description='Explicit management API update, or an extracted quote from the current user request.')
    run_id: str | None = Field(default=None, description='Source public Run ID for Agent extraction. No new Run is created for manual memory edits.')
    session_id: str | None = Field(default=None, description='Source session for audit only; entries are shared within this project.')

class MemoryEntry(BaseModel):
    section: MemorySection = Field(description='Background, analysis/report preferences, or explicitly shared findings.')
    key: str = Field(description='Stable topic key within this section and project.')
    content: str = Field(description='Shared text. Empty on a deleted version marker; it never grants execution approval.')
    version: int = Field(description='Monotonically increasing topic version, including delete and restore operations.')
    is_deleted: bool = Field(description='Deleted entries are excluded from usable knowledge; their versions prevent stale recreation.')
    source: MemorySource = Field(description='The service-assigned provenance of the latest update.')
    updated_at: str = Field(description='UTC ISO timestamp of the latest update.')

class MemoryResource(BaseModel):
    schema_version: Literal[1] = Field(description='Project memory document format version.')
    user_id: str = Field(description='Internal owner UUID. Caller identity still comes from the authenticated SSO session.')
    project_id: str = Field(description='The one project whose sessions can use this document.')
    entries: list[MemoryEntry] = Field(description='Bounded topics and deleted version markers, sorted by section/key.')

class MemoryWrittenEntry(BaseModel):
    section: MemorySection
    key: str
    version: int = Field(description='Committed topic version after this write, or the original version on idempotent replay.')
    is_deleted: bool = Field(description='True when this write deleted the topic.')

class MemoryWriteResult(BaseModel):
    status: Literal['saved'] = Field(description='This write committed, or an identical idempotent request was already committed.')
    entries: list[MemoryWrittenEntry] = Field(description='Written topics; no model-generated content is echoed as a save receipt.')
