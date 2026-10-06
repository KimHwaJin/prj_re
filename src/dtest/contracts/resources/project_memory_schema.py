"""One project-owned Markdown resource; no topic IDs or keys in public API."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from dtest.contracts.project_memory import MAX_STORAGE_CHARS

class MemoryPut(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    content: str = Field(max_length=MAX_STORAGE_CHARS, description='Complete project Markdown document. Empty clears it. Configured max_chars applies to content, not metadata.')
    expected_version: int = Field(ge=0, description='Echo the GET version. 0 only before the first write; reset never returns it to 0.')

class MemoryResource(BaseModel):
    schema_version: Literal[2] = Field(description='Single Markdown document format.')
    project_id: str = Field(description='Project owning this one memory resource; no separate memory ID.')
    content: str = Field(description='Reference Markdown shared across project sessions. Empty before first write or after reset; not execution approval.')
    version: int = Field(ge=0, description='Document version, increasing on every committed change/reset. Unchanged PUT does not increment it.')
    updated_at: str | None = Field(description='UTC ISO timestamp of the latest change; null before the first write.')
