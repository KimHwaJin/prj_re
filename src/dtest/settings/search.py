"""One model space and bounded ANN policy, loaded only by service_settings."""

from hashlib import sha256
import json
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class WorkflowSearchSettings(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True, frozen=True, extra="forbid", allow_inf_nan=False
    )
    base_url: str | None = Field(
        default=None, validation_alias="WORKFLOW_EMBEDDING_BASE_URL"
    )
    api_key: SecretStr = Field(
        default_factory=lambda: SecretStr(""),
        validation_alias="WORKFLOW_EMBEDDING_API_KEY",
    )
    model: str | None = Field(
        default=None, validation_alias="WORKFLOW_EMBEDDING_MODEL"
    )
    model_revision: str = Field(
        default="default",
        min_length=1,
        max_length=100,
        validation_alias="WORKFLOW_EMBEDDING_MODEL_REVISION",
    )
    dimensions: int | None = Field(
        default=None,
        ge=1,
        le=2000,
        validation_alias="WORKFLOW_EMBEDDING_DIMENSIONS",
    )
    embedding_timeout_seconds: float = Field(
        default=15,
        gt=0,
        le=120,
        validation_alias="WORKFLOW_EMBEDDING_TIMEOUT_SECONDS",
    )
    embedding_concurrency: int = Field(
        default=2,
        ge=1,
        le=32,
        validation_alias="WORKFLOW_EMBEDDING_CONCURRENCY",
    )
    embedding_batch_size: int = Field(
        default=32,
        ge=1,
        le=256,
        validation_alias="WORKFLOW_EMBEDDING_BATCH_SIZE",
    )
    context_max_chars: int = Field(default=64000, ge=1000, le=256000)
    candidate_limit: int = Field(default=20, ge=1, le=100)
    batch_size: int = Field(default=64, ge=1, le=2048)
    max_rounds: int = Field(default=8, ge=1, le=32)
    timeout_ms: int = Field(default=2000, ge=10, le=30000)
    ef_search: int = Field(default=200, ge=1, le=1000)
    max_scan_tuples: int = Field(default=20000, ge=1, le=1000000)
    scan_mem_multiplier: int = Field(default=2, ge=1, le=16)

    @model_validator(mode="after")
    def complete_space(self):
        supplied = [
            self.base_url is not None,
            self.model is not None,
            self.dimensions is not None,
        ]
        if any(supplied) and not all(supplied):
            raise ValueError(
                "WORKFLOW_EMBEDDING_BASE_URL, MODEL and DIMENSIONS "
                "must be configured "
                "together"
            )
        if self.base_url is not None:
            from urllib.parse import urlsplit

            url = urlsplit(self.base_url)
            if (
                url.scheme not in {"http", "https"}
                or not url.netloc
                or url.username
                or url.password
                or url.query
                or url.fragment
            ):
                raise ValueError("Invalid WORKFLOW_EMBEDDING_BASE_URL")
        if self.model is not None and not self.model.strip():
            raise ValueError("WORKFLOW_EMBEDDING_MODEL must not be blank")
        return self

    @property
    def configured(self):
        return self.model is not None

    @property
    def space(self):
        if not self.configured:
            raise ValueError("Embedding model space is not configured")
        return sha256(
            json.dumps(
                [
                    self.base_url.rstrip("/"),
                    self.model,
                    self.model_revision,
                    self.dimensions,
                ],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()

    @property
    def index_name(self):
        return "ix_workflow_hnsw_" + self.space[:24]

    @property
    def predicate(self):
        # Both interpolated values are generated/validated here, never request data.
        return f"is_active = true AND status = 'ready' AND model_space = '{self.space}' AND dimensions = {self.dimensions}"

    def index_sql(self):
        return f"CREATE INDEX IF NOT EXISTS {self.index_name} ON workflow_embeddings USING hnsw ((vector_values::vector({self.dimensions})) vector_cosine_ops) WITH (m=16, ef_construction=128) WHERE {self.predicate}"
