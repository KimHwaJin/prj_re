"""Transport-neutral retrieval result and Agent port; no service DB dependency."""

from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WorkflowSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=4000)

    @field_validator("query")
    @classmethod
    def nonblank_query(cls, value):
        if not value.strip():
            raise ValueError("query must not be blank")
        return value.strip()


class WorkflowSearchCandidate(BaseModel):
    workflow_id: str
    content_sha256: str
    resource_revision: int
    search_revision: int
    name: str
    similarity: float
    matched_query: str
    document: dict[str, Any]


class WorkflowSearchDiagnostics(BaseModel):
    mode: Literal["hnsw"] = "hnsw"
    approximate: Literal[True] = True
    termination: Literal[
        "candidate_limit",
        "no_more_ann_candidates",
        "round_limit",
        "timeout",
        "disabled",
        "unconfigured",
        "index_unavailable",
        "embedding_unavailable",
        "database_unavailable",
    ]
    rounds: int = 0
    ann_rows: int = 0
    distinct_candidates: int = 0
    elapsed_ms: float = 0
    reranked: bool = False


class WorkflowSearchResult(BaseModel):
    items: list[WorkflowSearchCandidate] = Field(default_factory=list)
    diagnostics: WorkflowSearchDiagnostics


class WorkflowRetriever(Protocol):
    async def search(self, query: str) -> WorkflowSearchResult: ...
