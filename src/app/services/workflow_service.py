from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.enums import AgentRunStatus
from app.models.common.agent_run_model import AgentRunModel
from app.models.common.session_model import SessionModel
from app.models.common.workflow_model import WorkflowModel, WorkflowTagModel
from app.schemas.common.workflow_schema import WorkflowCandidateCreate, WorkflowClone, WorkflowUpdate
from agent_service.agents.analysis.schemas.workflows.workflow_format import WorkflowDefinition, WorkflowStatus
from app.services.helpers import utc_now
from app.services.workflow_file_store import WorkflowFileStore


class WorkflowService:
    """E13 DB 메타데이터와 JSON 파일의 일관성 및 전역 template 접근 정책을 담당합니다."""

    @staticmethod
    def _definition(document: dict[str, Any]) -> WorkflowDefinition:
        raw = document.get("workflow", document)
        if not isinstance(raw, dict):
            raise HTTPException(status_code=422, detail="document.workflow must be an object.")
        raw = dict(raw)
        raw.pop("runtime_decisions", None)
        try:
            return WorkflowDefinition.model_validate(raw)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid workflow document: {exc}") from exc

    @staticmethod
    async def _owned_run(db: AsyncSession, user_id: UUID, run_id: UUID) -> AgentRunModel:
        run = await db.scalar(
            select(AgentRunModel)
            .join(SessionModel, SessionModel.session_id == AgentRunModel.session_id)
            .where(AgentRunModel.run_id == run_id, SessionModel.user_id == user_id)
        )
        if run is None:
            raise HTTPException(status_code=404, detail="Source run not found.")
        return run

    @staticmethod
    def _visibility(user_id: UUID):
        # 성공 승격 template은 서비스 전체 공개, candidate는 생성자에게만 공개합니다.
        return or_(WorkflowModel.lifecycle == "template", WorkflowModel.created_by_user_id == user_id)

    @staticmethod
    async def get(db: AsyncSession, user_id: UUID, workflow_id: UUID, *, include_deleted: bool = False):
        conditions = [WorkflowModel.workflow_id == workflow_id, WorkflowService._visibility(user_id)]
        if not include_deleted:
            conditions.append(WorkflowModel.deleted_at.is_(None))
        workflow = await db.scalar(
            select(WorkflowModel).options(selectinload(WorkflowModel.tags)).where(*conditions)
        )
        if workflow is None:
            raise HTTPException(status_code=404, detail="Workflow not found.")
        return workflow

    @staticmethod
    async def create_candidate(
        db: AsyncSession, user_id: UUID, payload: WorkflowCandidateCreate
    ) -> WorkflowModel:
        definition = WorkflowService._definition(payload.document)
        source_run = await WorkflowService._owned_run(db, user_id, payload.source_run_id)
        # 기존 template을 추천/선택한 실행은 같은 Workflow를 다시 자산화하지 않습니다.
        if (source_run.agent_response or {}).get("workflow_origin") != "generated":
            raise HTTPException(
                status_code=409,
                detail="Only an LLM-generated workflow can be registered as a candidate.",
            )
        existing = await db.scalar(
            select(WorkflowModel)
            .options(selectinload(WorkflowModel.tags))
            .where(
                WorkflowModel.source_run_id == payload.source_run_id,
                WorkflowModel.lifecycle == "candidate",
                WorkflowModel.source_workflow_id.is_(None),
            )
        )
        if existing is not None:
            # 성공 응답 재시도에서도 같은 root candidate로 수렴합니다.
            return existing
        workflow_id = uuid4()
        path, checksum = WorkflowFileStore.write(workflow_id, payload.document)
        workflow = WorkflowModel(
            workflow_id=workflow_id,
            name=definition.name,
            description=definition.description,
            goal=definition.goal,
            schema_version=str(payload.document.get("schema_version", "1.0")),
            lifecycle="candidate",
            file_path=path,
            content_sha256=checksum,
            source_run_id=payload.source_run_id,
            created_by_user_id=user_id,
            is_recommendable=False,
            tags=[WorkflowTagModel(tag=tag) for tag in payload.tags],
        )
        db.add(workflow)
        try:
            await db.commit()
        except Exception:
            await db.rollback()
            WorkflowFileStore.remove_if_exists(path)
            raise
        await db.refresh(workflow, attribute_names=["tags"])
        return workflow

    @staticmethod
    async def save_generated_after_success(
        db: AsyncSession,
        *,
        user_id: UUID,
        source_run_id: UUID,
        document: dict[str, Any],
    ) -> WorkflowModel:
        """Graph 성공 후 호출하는 단일 진입점. create_candidate의 origin 검사를 재사용합니다."""
        return await WorkflowService.create_candidate(
            db,
            user_id,
            WorkflowCandidateCreate(
                source_run_id=source_run_id,
                document=document,
                tags=[],
            ),
        )

    @staticmethod
    async def promote(db: AsyncSession, user_id: UUID, workflow_id: UUID) -> WorkflowModel:
        candidate = await WorkflowService.get(db, user_id, workflow_id)
        if candidate.created_by_user_id != user_id or candidate.lifecycle != "candidate":
            raise HTTPException(status_code=409, detail="Only an owned candidate can be promoted.")
        source_run = await WorkflowService._owned_run(db, user_id, candidate.source_run_id)
        # Promotion Guard: 요청 payload가 아니라 영속화된 실행의 terminal success를 검사합니다.
        if source_run.status != AgentRunStatus.SUCCESS:
            raise HTTPException(status_code=409, detail="Only a candidate from a successful run can be promoted.")
        document = WorkflowFileStore.read(candidate.file_path)
        definition = WorkflowService._definition(document)
        if definition.status != WorkflowStatus.READY:
            raise HTTPException(status_code=409, detail="Only a ready workflow can be promoted.")

        promoted_id = uuid4()
        path, checksum = WorkflowFileStore.write(promoted_id, document)
        promoted = WorkflowModel(
            workflow_id=promoted_id,
            name=candidate.name,
            description=candidate.description,
            goal=candidate.goal,
            schema_version=candidate.schema_version,
            lifecycle="template",
            file_path=path,
            content_sha256=checksum,
            source_run_id=candidate.source_run_id,
            source_workflow_id=candidate.workflow_id,
            created_by_user_id=user_id,
            is_recommendable=True,
            tags=[WorkflowTagModel(tag=item.tag) for item in candidate.tags],
        )
        db.add(promoted)
        try:
            await db.commit()
        except Exception:
            await db.rollback()
            WorkflowFileStore.remove_if_exists(path)
            raise
        await db.refresh(promoted, attribute_names=["tags"])
        return promoted

    @staticmethod
    async def clone(
        db: AsyncSession, user_id: UUID, workflow_id: UUID, payload: WorkflowClone
    ) -> WorkflowModel:
        source = await WorkflowService.get(db, user_id, workflow_id)
        document = WorkflowFileStore.read(source.file_path)
        clone_id = uuid4()
        path, checksum = WorkflowFileStore.write(clone_id, document)
        tags = payload.tags if payload.tags is not None else [item.tag for item in source.tags]
        clone = WorkflowModel(
            workflow_id=clone_id,
            name=payload.name or f"{source.name} copy",
            description=source.description,
            goal=source.goal,
            schema_version=source.schema_version,
            lifecycle="candidate",
            file_path=path,
            content_sha256=checksum,
            source_run_id=source.source_run_id,
            source_workflow_id=source.workflow_id,
            created_by_user_id=user_id,
            is_recommendable=False,
            tags=[WorkflowTagModel(tag=tag) for tag in tags],
        )
        db.add(clone)
        try:
            await db.commit()
        except Exception:
            await db.rollback()
            WorkflowFileStore.remove_if_exists(path)
            raise
        await db.refresh(clone, attribute_names=["tags"])
        return clone

    @staticmethod
    async def update(
        db: AsyncSession, user_id: UUID, workflow_id: UUID, payload: WorkflowUpdate
    ) -> WorkflowModel:
        workflow = await WorkflowService.get(db, user_id, workflow_id)
        if workflow.created_by_user_id != user_id:
            raise HTTPException(status_code=403, detail="Only the creator can update this workflow.")
        if payload.name is not None:
            workflow.name = payload.name.strip()
        if payload.description is not None:
            workflow.description = payload.description
        if payload.tags is not None:
            workflow.tags = [WorkflowTagModel(tag=tag) for tag in payload.tags]
        await db.commit()
        await db.refresh(workflow, attribute_names=["tags"])
        return workflow

    @staticmethod
    async def delete(db: AsyncSession, user_id: UUID, workflow_id: UUID) -> None:
        workflow = await WorkflowService.get(db, user_id, workflow_id)
        if workflow.created_by_user_id != user_id:
            raise HTTPException(status_code=403, detail="Only the creator can delete this workflow.")
        # Soft Delete이므로 JSON 원본도 감사/복구를 위해 그대로 보존합니다.
        workflow.deleted_at = utc_now()
        await db.commit()

