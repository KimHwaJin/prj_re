"""042 offline Dataset Registry contract draft; no production API/backend is wired.

Mechanical metadata is supplied by Executor/storage evidence. Agent annotations
are separate. Helpers validate a proposed contract, not the existence of a file.
JSON Schema is exported for reuse without installing the Agent's dependencies.
"""

import unicodedata
from enum import Enum
from pathlib import PurePosixPath
from typing import Literal, Protocol
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

VERSION = "dataset-registry.v1-draft"


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class DataScope(str, Enum):
    USER = "USER"
    PROJECT = "PROJECT"
    SESSION = "SESSION"


class RequestContext(ContractModel):
    """Agent derives these from its authorized Session, not arbitrary frontend IDs."""

    user_id: UUID
    project_id: UUID
    session_id: UUID


class ScopeOwner(ContractModel):
    scope: DataScope = DataScope.PROJECT
    user_id: UUID
    project_id: UUID | None = None
    session_id: UUID | None = None

    @model_validator(mode="after")
    def shape(self):
        if self.scope == DataScope.USER and (
            self.project_id or self.session_id
        ):
            raise ValueError("USER scope carries only user_id")
        if self.scope == DataScope.PROJECT and (
            not self.project_id or self.session_id
        ):
            raise ValueError(
                "PROJECT scope requires project_id and forbids session_id"
            )
        if self.scope == DataScope.SESSION and (
            not self.project_id or not self.session_id
        ):
            raise ValueError(
                "SESSION scope requires project_id and session_id"
            )
        return self


def owner_for(
    context: RequestContext, scope: DataScope = DataScope.PROJECT
) -> ScopeOwner:
    return ScopeOwner(
        scope=scope,
        user_id=context.user_id,
        project_id=context.project_id if scope != DataScope.USER else None,
        session_id=context.session_id if scope == DataScope.SESSION else None,
    )


def data_directory(owner: ScopeOwner) -> str:
    """Proposed Runtime-relative layout, under the configured shared PVC root.

    Mirrors Executor's current plural users/projects/sessions naming. This does
    not create folders or change the current Agent's singular data directory.
    """
    root = PurePosixPath("users") / str(owner.user_id)
    if owner.project_id:
        root = root / "projects" / str(owner.project_id)
    if owner.session_id:
        root = root / "sessions" / str(owner.session_id)
    return (root / "datasets").as_posix()


def canonical_path(value: str, *, absolute: bool) -> str:
    if (
        not value
        or "\\" in value
        or any(unicodedata.category(c) == "Cc" for c in value)
    ):
        raise ValueError("Invalid Runtime path")
    path = PurePosixPath(value)
    parts = value.split("/")[1:] if absolute else value.split("/")
    if (
        path.is_absolute() != absolute
        or not parts
        or any(p in {"", ".", ".."} for p in parts)
    ):
        raise ValueError(
            "Runtime path must be canonical and cannot traverse parents"
        )
    if path.as_posix() != value:
        raise ValueError("Runtime path must be canonical")
    return value


class StoragePolicy(ContractModel):
    """Internal deployment mapping: namespace means logical shared storage, not Pod/kernel ID."""

    namespace: str = Field(
        min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
    )
    runtime_root: str = Field(min_length=2, max_length=2000)

    @field_validator("runtime_root")
    @classmethod
    def root(cls, value):
        return canonical_path(value, absolute=True)


class DatasetRef(ContractModel):
    dataset_id: UUID
    version: int = Field(ge=1, le=9223372036854775807, strict=True)


def selection_id(reference: DatasetRef) -> str:
    """Versioned opaque string compatible with existing data_reference inputs."""
    return f"pvc:{reference.dataset_id}:{reference.version}"


def parse_selection_id(value: str) -> DatasetRef:
    if not isinstance(value, str):
        raise ValueError("Expected a versioned PVC selection ID")
    try:
        prefix, identity, version = value.split(":")
        result = DatasetRef(dataset_id=UUID(identity), version=int(version))
    except (ValueError, TypeError) as exc:
        raise ValueError("Expected a versioned PVC selection ID") from exc
    if prefix != "pvc" or selection_id(result) != value:
        raise ValueError(
            "PVC selection IDs must use canonical UUID and an "
            "explicit positive "
            "version"
        )
    return result


class ColumnInfo(ContractModel):
    index: int = Field(ge=0, strict=True)
    name: str = Field(min_length=1, max_length=500)
    dtype: str = Field(min_length=1, max_length=2000)


class ParquetInspection(ContractModel):
    """Executor-owned bounded footer facts. No full data/head arrays in v1."""

    status: Literal["AVAILABLE", "UNAVAILABLE"]
    rows: int | None = Field(default=None, ge=0, strict=True)
    column_count: int | None = Field(default=None, ge=0, strict=True)
    columns: list[ColumnInfo] = Field(default_factory=list, max_length=200)
    truncated: bool = False
    reason: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def measured(self):
        if self.status == "UNAVAILABLE":
            if (
                self.rows is not None
                or self.column_count is not None
                or self.columns
                or self.truncated
            ):
                raise ValueError(
                    "Unavailable inspection cannot contain invented "
                    "schema facts"
                )
            if not self.reason:
                raise ValueError(
                    "Unavailable inspection needs a bounded reason"
                )
        else:
            if (
                self.rows is None
                or self.column_count is None
                or self.reason is not None
            ):
                raise ValueError(
                    "Available inspection requires measured rows "
                    "and column "
                    "count"
                )
            indices = [c.index for c in self.columns]
            if len(indices) != len(set(indices)) or any(
                i >= self.column_count for i in indices
            ):
                raise ValueError(
                    "Inspection column indices must be unique and in range"
                )
            if self.truncated != (len(self.columns) < self.column_count):
                raise ValueError("Inspection must disclose omitted columns")
        return self


class FileEvidence(ContractModel):
    """Private facts from the storage driver. A stat/footer token is not a full-file SHA proof."""

    storage_namespace: str = Field(min_length=1, max_length=120)
    relative_path: str = Field(min_length=1, max_length=4000)
    size_bytes: int = Field(ge=1, strict=True)
    revision: str = Field(min_length=1, max_length=255)
    revision_method: Literal[
        "STAT_AND_FOOTER", "STORAGE_VERSION", "WRITER_SHA256"
    ]
    format: Literal["parquet"] = "parquet"

    @field_validator("relative_path")
    @classmethod
    def path(cls, value):
        return canonical_path(value, absolute=False)


class ProducerEvidence(ContractModel):
    execution_id: UUID
    operation_id: UUID
    step_id: UUID
    execution_attempt_id: UUID
    step_attempt_id: UUID
    sequence: int = Field(ge=0, strict=True)
    fencing_token: int = Field(ge=1, strict=True)
    context: RequestContext
    step_status: Literal["SUCCEEDED", "FAILED", "NOT_RUN"]


class DatasetCandidate(ContractModel):
    schema_version: Literal["dataset-registry.v1-draft"] = VERSION
    candidate_id: UUID
    owner: ScopeOwner
    producer: ProducerEvidence
    publication: Literal["FINALIZED", "WRITING", "UNKNOWN"]
    file: FileEvidence
    inspection: ParquetInspection

    @model_validator(mode="after")
    def owned_file(self):
        if self.owner != owner_for(self.producer.context, self.owner.scope):
            raise ValueError(
                "Candidate scope must match the actual producing Execution"
            )
        root = PurePosixPath(data_directory(self.owner))
        path = PurePosixPath(self.file.relative_path)
        if not path.is_relative_to(root) or path == root:
            raise ValueError("Candidate file leaves its owned dataset root")
        if path.suffix.lower() != ".parquet":
            raise ValueError(
                "Initial processed Dataset contract supports Parquet only"
            )
        return self


class AgentAnnotation(ContractModel):
    """Agent interpretations; never supplied as measured rows/column types/file identity."""

    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    purpose: str = Field(default="", max_length=2000)
    notes: list[str] = Field(default_factory=list, max_length=20)
    declared_parents: list[DatasetRef] = Field(
        default_factory=list, max_length=100
    )

    @field_validator("title")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Dataset title must contain text")
        return value.strip()

    @field_validator("notes")
    @classmethod
    def bounded_notes(cls, value):
        if any(len(note) > 1000 for note in value):
            raise ValueError("Annotation note is too long")
        return value


class DatasetRegistration(ContractModel):
    """Proposed metadata registration body. File paths, owner IDs and schema are forbidden."""

    idempotency_key: str = Field(min_length=1, max_length=255)
    candidate_id: UUID
    expected_file_revision: str = Field(min_length=1, max_length=255)
    scope: DataScope = DataScope.PROJECT
    annotation: AgentAnnotation


class DatasetRecord(ContractModel):
    schema_version: Literal["dataset-registry.v1-draft"] = VERSION
    ref: DatasetRef
    candidate: DatasetCandidate
    annotation: AgentAnnotation
    status: Literal["AVAILABLE", "STALE", "UNAVAILABLE"] = "AVAILABLE"

    @model_validator(mode="after")
    def ready(self):
        c = self.candidate
        if self.status == "AVAILABLE" and (
            c.producer.step_status != "SUCCEEDED"
            or c.publication != "FINALIZED"
            or c.inspection.status != "AVAILABLE"
        ):
            raise ValueError(
                "Available Dataset requires completed Step, "
                "published file and measured Parquet "
                "metadata"
            )
        return self


class DatasetView(ContractModel):
    """Only this projection is passed to the model/HITL; no paths, file tokens or code."""

    ref: DatasetRef
    selection_id: str = Field(min_length=1, max_length=80)
    source: Literal["PREPROCESSED"] = "PREPROCESSED"
    title: str
    description: str
    purpose: str
    scope: DataScope
    status: Literal["AVAILABLE", "STALE", "UNAVAILABLE"]
    size_bytes: int
    inspection: ParquetInspection

    @model_validator(mode="after")
    def canonical_selection(self):
        if self.selection_id != selection_id(self.ref):
            raise ValueError(
                "Selection ID must retain the exact Dataset version"
            )
        return self


class RuntimeDatasetBinding(ContractModel):
    """Private approval binding. Kept outside public view/Agent-generated requests."""

    ref: DatasetRef
    owner: ScopeOwner
    storage_namespace: str
    runtime_path: str
    expected_file_revision: str
    revision_method: Literal[
        "STAT_AND_FOOTER", "STORAGE_VERSION", "WRITER_SHA256"
    ]


class DatasetPage(ContractModel):
    schema_version: Literal["dataset-registry.v1-draft"] = VERSION
    items: list[DatasetView] = Field(default_factory=list, max_length=100)
    next_cursor: str | None = None


def accessible(owner: ScopeOwner, context: RequestContext) -> bool:
    return owner.user_id == context.user_id and (
        owner.scope == DataScope.USER
        or owner.project_id == context.project_id
        and (
            owner.scope == DataScope.PROJECT
            or owner.session_id == context.session_id
        )
    )


def register_candidate(
    request: DatasetRegistration,
    candidate: DatasetCandidate,
    *,
    execution_id: UUID,
    ref: DatasetRef,
    context: RequestContext,
    approved_parent_refs: list[DatasetRef],
) -> DatasetRecord:
    """Offline acceptance rules. Caller must obtain evidence from Executor, not the LLM.

    Does not write data.json/DB, check filesystem existence or implement idempotency.
    Whole Execution status deliberately is not a registration precondition.
    """
    if (
        candidate.producer.execution_id != execution_id
        or candidate.candidate_id != request.candidate_id
    ):
        raise ValueError("Candidate does not belong to this Execution")
    if candidate.producer.context != context or candidate.owner != owner_for(
        context, request.scope
    ):
        raise ValueError(
            "Registration cannot reassign a producing context or "
            "broaden its "
            "scope"
        )
    if candidate.file.revision != request.expected_file_revision:
        raise ValueError("Candidate file revision changed")
    allowed = {(p.dataset_id, p.version) for p in approved_parent_refs}
    parents = [
        (p.dataset_id, p.version) for p in request.annotation.declared_parents
    ]
    if len(parents) != len(set(parents)) or not set(parents) <= allowed:
        raise ValueError(
            "Declared parents must reference approved input Dataset versions"
        )
    return DatasetRecord(
        ref=ref,
        candidate=candidate.model_copy(deep=True),
        annotation=request.annotation.model_copy(deep=True),
    )


def public_dataset(
    record: DatasetRecord, context: RequestContext
) -> DatasetView:
    if not accessible(record.candidate.owner, context):
        raise ValueError("Dataset is unknown or inaccessible")
    return DatasetView(
        ref=record.ref.model_copy(deep=True),
        selection_id=selection_id(record.ref),
        title=record.annotation.title,
        description=record.annotation.description,
        purpose=record.annotation.purpose,
        scope=record.candidate.owner.scope,
        status=record.status,
        size_bytes=record.candidate.file.size_bytes,
        inspection=record.candidate.inspection.model_copy(deep=True),
    )


def resolve_binding(
    record: DatasetRecord,
    reference: DatasetRef,
    context: RequestContext,
    policy: StoragePolicy,
    *,
    current_file: FileEvidence,
) -> RuntimeDatasetBinding:
    """Check an exact version against newly acquired storage evidence. Never uses latest.

    Storage adapters must obtain current_file after a fresh Runtime lookup; passing
    the old descriptor again does not establish that a file is still unchanged.
    Symlink and real storage containment checks belong to Executor's driver.
    """
    if not accessible(record.candidate.owner, context):
        raise ValueError("Dataset is unknown or inaccessible")
    if record.ref != reference or record.status != "AVAILABLE":
        raise ValueError("Dataset version is unknown, stale or unavailable")
    expected = record.candidate.file
    if expected.storage_namespace != policy.namespace:
        raise ValueError(
            "Selected Runtime cannot access this Dataset storage namespace"
        )
    if current_file != expected:
        raise ValueError(
            "Dataset file changed; select and approve a current version"
        )
    path = PurePosixPath(policy.runtime_root) / expected.relative_path
    return RuntimeDatasetBinding(
        ref=reference.model_copy(deep=True),
        owner=record.candidate.owner.model_copy(deep=True),
        storage_namespace=policy.namespace,
        runtime_path=path.as_posix(),
        expected_file_revision=expected.revision,
        revision_method=expected.revision_method,
    )


class DatasetRegistry(Protocol):
    """Future provider boundary; intentionally no implementation or startup hook."""

    async def candidates(
        self,
        execution_id: UUID,
        context: RequestContext,
        *,
        cursor: str | None,
        limit: int,
    ) -> list[DatasetCandidate]: ...
    async def register(
        self,
        execution_id: UUID,
        request: DatasetRegistration,
        context: RequestContext,
    ) -> DatasetRecord: ...
    async def list(
        self, context: RequestContext, *, cursor: str | None, limit: int
    ) -> DatasetPage: ...
    async def get(
        self, reference: DatasetRef, context: RequestContext
    ) -> DatasetRecord: ...
    async def resolve(
        self,
        reference: DatasetRef,
        context: RequestContext,
        expected_file_revision: str,
        storage_namespace: str,
    ) -> RuntimeDatasetBinding: ...


class ContractExamples(ContractModel):
    """Offline schema bundle only. This envelope is not a proposed REST endpoint."""

    context: RequestContext
    storage: StoragePolicy
    registration: DatasetRegistration
    candidate: DatasetCandidate
    record: DatasetRecord
    public_view: DatasetView
    binding: RuntimeDatasetBinding
    page: DatasetPage
