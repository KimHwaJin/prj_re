from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from dtest.infrastructure.database.models.model_base import (
    Base,
    TimestampMixin,
)


class WorkflowModel(TimestampMixin, Base):
    """E13 전역 Workflow 카탈로그. 사용자/Project/Session 외래키로 범위를 제한하지 않습니다."""

    __tablename__ = "workflows"

    workflow_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 추천 프롬프트와 FE 상세 설명에 사용하는 Workflow의 최종 목적입니다.
    goal: Mapped[str] = mapped_column(Text, nullable=False, default="")
    schema_version: Mapped[str] = mapped_column(
        String(30), nullable=False, default="1.0"
    )
    # candidate는 작성자 전용 후보, template은 승격된 전역 자산입니다. 공개 2.0은 실행 성공을 요구하지 않습니다.
    lifecycle: Mapped[str] = mapped_column(
        String(20), nullable=False, default="candidate", index=True
    )
    file_path: Mapped[str] = mapped_column(
        String(1000), nullable=False, unique=True
    )
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    user_queries: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    resource_revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    search_revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    index_state: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="not_indexed",
        server_default="not_indexed",
    )
    index_error: Mapped[str | None] = mapped_column(String(100))
    source_run_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agent_runs.run_id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    # 복제/승격은 원본을 덮어쓰지 않고 새 행을 만들어 이 FK로 역추적합니다.
    source_workflow_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workflows.workflow_id", ondelete="RESTRICT"),
        index=True,
    )
    # 접근 범위용 FK가 아니라 후보 변경 권한과 감사 기록용입니다. template 조회는 전역입니다.
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="SET NULL"),
        index=True,
    )
    # 운영자가 특정 template을 검색 후보에서 즉시 제외할 수 있는 별도 스위치입니다.
    is_recommendable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    tags = relationship(
        "WorkflowTagModel",
        back_populates="workflow",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    source_workflow = relationship("WorkflowModel", remote_side=[workflow_id])
    embeddings = relationship(
        "WorkflowEmbeddingModel",
        back_populates="workflow",
        cascade="all, delete-orphan",
    )
    execution_logs = relationship(
        "WorkflowExecutionLogModel", back_populates="workflow"
    )

    __table_args__ = (
        CheckConstraint(
            "resource_revision > 0 AND search_revision > 0",
            name="ck_workflows_revisions",
        ),
        CheckConstraint(
            "index_state IN ('not_indexed', 'pending', 'ready', 'failed')",
            name="ck_workflows_index_state",
        ),
        CheckConstraint(
            "lifecycle IN ('candidate', 'template')",
            name="ck_workflows_lifecycle",
        ),
        Index(
            "ix_workflows_active_created",
            "lifecycle",
            "created_at",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # 동일 Run의 자동 저장/재시도는 root candidate 하나로 수렴합니다.
        Index(
            "uq_workflows_root_candidate_run",
            "source_run_id",
            unique=True,
            postgresql_where=text(
                "lifecycle = 'candidate' AND source_workflow_id IS NULL"
            ),
        ),
    )


class WorkflowTagModel(Base):
    """검색 가능한 정규화 태그. 한 Workflow 내 동일 태그를 DB 제약으로 차단합니다."""

    __tablename__ = "workflow_tags"

    workflow_tag_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    workflow_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workflows.workflow_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tag: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    workflow = relationship("WorkflowModel", back_populates="tags")

    __table_args__ = (
        UniqueConstraint(
            "workflow_id", "tag", name="uq_workflow_tags_workflow_tag"
        ),
    )


class WorkflowEmbeddingModel(Base):
    """추천 검색용 임베딩 버전 이력. Workflow 원본과 수명을 분리합니다."""

    __tablename__ = "workflow_embeddings"

    embedding_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    workflow_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workflows.workflow_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    search_revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    model_space: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 한 user_queries 항목을 그대로 임베딩합니다. JSON/코드는 임베딩 입력에 넣지 않습니다.
    embedded_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedded_text_sha256: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    search_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    model_provider: Mapped[str] = mapped_column(String(100), nullable=False)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    model_revision: Mapped[str] = mapped_column(
        String(100), nullable=False, default="default"
    )
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    # 모델 차원은 배포 설정으로 지정하며 해당 공간에만 고정 차원 HNSW expression index를 만듭니다.
    vector_values: Mapped[list[float] | None] = mapped_column(VECTOR())
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )
    failure_reason: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    embedded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    workflow = relationship("WorkflowModel", back_populates="embeddings")

    __table_args__ = (
        CheckConstraint(
            "dimensions > 0", name="ck_workflow_embeddings_dimensions"
        ),
        CheckConstraint(
            "status IN ('pending', 'ready', 'failed', 'superseded')",
            name="ck_workflow_embeddings_status",
        ),
        CheckConstraint(
            (
                "status <> 'ready' OR (vector_values IS NOT NULL AND "
                "vector_dims(vector_values) = "
                "dimensions)"
            ),
            name="ck_workflow_embeddings_ready_vector",
        ),
        UniqueConstraint(
            "workflow_id",
            "search_revision",
            "model_space",
            "embedded_text_sha256",
            name="uq_workflow_embeddings_source_model",
        ),
        Index(
            "ix_workflow_embeddings_search_metadata",
            "search_metadata",
            postgresql_using="gin",
        ),
    )


class WorkflowExecutionLogModel(Base):
    """Workflow 실행 사실과 결과만 남기는 append-only 감사 로그입니다."""

    __tablename__ = "workflow_execution_logs"

    workflow_log_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    workflow_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workflows.workflow_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    agent_run_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agent_runs.run_id", ondelete="SET NULL"),
        index=True,
    )
    task_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tasks.task_id", ondelete="SET NULL"),
        index=True,
    )
    execution_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), index=True
    )
    # 이 로그를 화면에 보여준 Message를 연결합니다. 상세 JSON은 로그에, 요약문은 Message에 둡니다.
    message_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("messages.message_id", ondelete="SET NULL"),
        index=True,
    )
    workflow_content_sha256: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    input_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    result: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    workflow = relationship("WorkflowModel", back_populates="execution_logs")

    __table_args__ = (
        CheckConstraint(
            "attempt_no > 0", name="ck_workflow_execution_logs_attempt"
        ),
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="ck_workflow_execution_logs_duration",
        ),
        CheckConstraint(
            (
                "status IN ('queued', 'running', 'interrupted', "
                "'success', 'failed', 'canceled', "
                "'timeout')"
            ),
            name="ck_workflow_execution_logs_status",
        ),
        UniqueConstraint(
            "workflow_id",
            "agent_run_id",
            "attempt_no",
            name="uq_workflow_execution_logs_attempt",
        ),
    )
