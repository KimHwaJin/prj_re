from __future__ import annotations

from typing import Any
import asyncio
from copy import deepcopy
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api_service.core.enums import AgentRunStatus
from api_service.models.common.agent_run_model import AgentRunModel
from api_service.models.common.session_model import SessionModel
from api_service.models.common.workflow_model import WorkflowModel, WorkflowTagModel, WorkflowEmbeddingModel
from api_service.schemas.common.workflow_schema import WorkflowCandidateCreate, WorkflowClone, WorkflowUpdate
from service_contracts.workflow_definition import WorkflowDefinition, WorkflowStatus
from api_service.services.helpers import utc_now
from api_service.services.workflow_file_store import WorkflowFileStore
from api_service.services.agent_graph_service import deployed_analysis_assets
from service_contracts.workflow_standard import normalize


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
    def _public(document):
        return document.get('workflow_version') == '2.0'

    @staticmethod
    async def _describe(document):
        if WorkflowService._public(document):
            try:
                plan = await asyncio.to_thread(lambda: normalize(document, deployed_analysis_assets().metadata))
            except (ValueError, KeyError, TypeError) as exc:
                raise HTTPException(status_code=422, detail=f'Invalid standard Workflow: {exc}') from exc
            return plan['name'], plan['description'], plan['goal'], '2.0'
        if 'workflow_version' in document:
            raise HTTPException(status_code=422, detail='Unsupported workflow_version; received 1.0 must be explicitly migrated, not reinterpreted.')
        definition = WorkflowService._definition(document)
        return definition.name, definition.description, definition.goal, str(document.get('schema_version', '1.3'))

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
        # Promoted templates are public; candidates are visible only to their author.
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
        name, description, goal, version = await WorkflowService._describe(payload.document)
        source_run = None
        if payload.source_run_id is not None:
            source_run = await WorkflowService._owned_run(db, user_id, payload.source_run_id)
        elif not WorkflowService._public(payload.document):
            raise HTTPException(status_code=422, detail='Legacy Workflow requires source_run_id.')
        # 기존 template을 추천/선택한 실행은 같은 Workflow를 다시 자산화하지 않습니다.
        if not WorkflowService._public(payload.document) and (source_run.agent_response or {}).get("workflow_origin") != "generated":
            raise HTTPException(
                status_code=409,
                detail="Only an LLM-generated workflow can be registered as a candidate.",
            )
        existing = await db.scalar(
            select(WorkflowModel)
            .options(selectinload(WorkflowModel.tags))
            .where(
                WorkflowModel.source_run_id == payload.source_run_id,
                WorkflowModel.source_run_id.is_not(None),
                WorkflowModel.lifecycle == "candidate",
                WorkflowModel.source_workflow_id.is_(None),
            )
        )
        if payload.source_run_id is not None and existing is not None:
            # 같은 출처 Run의 다른 등록 내용은 조용히 무시하지 않습니다.
            prior = await asyncio.to_thread(WorkflowFileStore.read, existing.file_path)
            if prior != payload.document or existing.user_queries != payload.user_queries:
                raise HTTPException(status_code=409, detail="Source Run already has a different Workflow candidate.")
            return existing
        workflow_id = uuid4()
        path, checksum = await asyncio.to_thread(WorkflowFileStore.write, workflow_id, payload.document)
        workflow = WorkflowModel(
            workflow_id=workflow_id,
            name=name,
            description=description,
            goal=goal,
            schema_version=version,
            lifecycle="candidate",
            file_path=path,
            content_sha256=checksum,
            source_run_id=payload.source_run_id,
            created_by_user_id=user_id,
            is_recommendable=False,
            tags=[WorkflowTagModel(tag=tag) for tag in payload.tags],
            user_queries=payload.user_queries,
            index_state="pending",
        )
        db.add(workflow)
        try:
            await db.commit()
        except Exception:
            await db.rollback()
            WorkflowFileStore.remove_if_exists(path)
            raise
        await db.refresh(workflow, attribute_names=["tags", "updated_at"])
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
                user_queries=[(await WorkflowService._describe(document))[2]],
                tags=[],
            ),
        )

    @staticmethod
    async def promote(db: AsyncSession, user_id: UUID, workflow_id: UUID) -> WorkflowModel:
        candidate = await WorkflowService.get(db, user_id, workflow_id)
        if candidate.created_by_user_id != user_id or candidate.lifecycle != "candidate":
            raise HTTPException(status_code=409, detail="Only an owned candidate can be promoted.")
        document = await asyncio.to_thread(WorkflowFileStore.read, candidate.file_path)
        if WorkflowService._public(document):
            await WorkflowService._describe(document)
        else:
            source_run = await WorkflowService._owned_run(db, user_id, candidate.source_run_id)
            if source_run.status != AgentRunStatus.SUCCESS:
                raise HTTPException(status_code=409, detail='Legacy candidate requires a successful source Run.')
            definition = WorkflowService._definition(document)
            if definition.status != WorkflowStatus.READY:
                raise HTTPException(status_code=409, detail='Legacy candidate must be ready.')

        promoted_id = uuid4()
        path, checksum = await asyncio.to_thread(WorkflowFileStore.write, promoted_id, document)
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
            user_queries=list(candidate.user_queries),
            index_state="pending",
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
        document = await asyncio.to_thread(WorkflowFileStore.read, source.file_path)
        clone_id = uuid4()
        if WorkflowService._public(document):
            document = deepcopy(document)
            document['workflow']['name'] = payload.name or f'{source.name[:195]} copy'
        path, checksum = await asyncio.to_thread(WorkflowFileStore.write, clone_id, document)
        tags = payload.tags if payload.tags is not None else [item.tag for item in source.tags]
        clone = WorkflowModel(
            workflow_id=clone_id,
            name=payload.name or f"{source.name[:195]} copy",
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
            user_queries=list(source.user_queries),
            index_state="pending",
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
        # Lock only the row being edited; concurrent content updates compare the
        # fresh persisted SHA after acquiring this lock, not a stale ORM value.
        workflow = await db.scalar(select(WorkflowModel).options(selectinload(WorkflowModel.tags))
            .where(WorkflowModel.workflow_id == workflow_id).with_for_update()
            .execution_options(populate_existing=True))
        if workflow is None or workflow.deleted_at is not None:
            raise HTTPException(status_code=404, detail='Workflow not found.')
        if payload.expected_resource_revision is not None and payload.expected_resource_revision != workflow.resource_revision:
            raise HTTPException(status_code=409, detail="Workflow changed; reload before updating.")
        previous_path = workflow.file_path
        replacement_path = None
        created_revision = False
        if payload.expected_content_sha256 is not None and payload.expected_content_sha256 != workflow.content_sha256:
            raise HTTPException(status_code=409, detail='Workflow changed; reload before updating.')
        content_edit = payload.document is not None or (workflow.schema_version == '2.0' and (payload.name is not None or payload.description is not None))
        if content_edit:
            if payload.expected_content_sha256 is None and payload.expected_resource_revision is None:
                raise HTTPException(status_code=422, detail="Content update requires expected_resource_revision or expected_content_sha256.")
            document = deepcopy(payload.document) if payload.document is not None else await asyncio.to_thread(WorkflowFileStore.read, previous_path)
            if not WorkflowService._public(document):
                raise HTTPException(status_code=422, detail='Content replacement requires public Workflow 2.0.')
            if payload.name is not None: document['workflow']['name'] = payload.name
            if payload.description is not None: document['workflow']['description'] = payload.description
            name, description, goal, version = await WorkflowService._describe(document)
            # Every edit gets a new immutable file; old approved revisions stay
            # readable. A transaction rollback cannot damage the previous file.
            replacement_path, checksum, created_revision = await asyncio.to_thread(WorkflowFileStore.write_revision, workflow_id, document)
            workflow.file_path, workflow.content_sha256 = replacement_path, checksum
            workflow.name, workflow.description, workflow.goal, workflow.schema_version = name, description, goal, version

        else:
            if payload.name is not None: workflow.name = payload.name.strip()
            if payload.description is not None: workflow.description = payload.description
        query_edit = payload.user_queries is not None and payload.user_queries != workflow.user_queries
        if query_edit:
            workflow.user_queries = payload.user_queries
        if content_edit or query_edit:
            workflow.search_revision += 1
            workflow.index_state = "pending"
            workflow.index_error = None
            await db.execute(update(WorkflowEmbeddingModel).where(
                WorkflowEmbeddingModel.workflow_id == workflow_id,
                WorkflowEmbeddingModel.status != "superseded"
            ).values(is_active=False, status="superseded"))
        workflow.resource_revision += 1
        if payload.tags is not None:
            existing_tags = {item.tag: item for item in workflow.tags}
            workflow.tags = [existing_tags.get(tag) or WorkflowTagModel(tag=tag) for tag in payload.tags]
        try:
            await db.commit()
        except Exception:
            await db.rollback()
            if replacement_path is not None and created_revision:
                await asyncio.to_thread(WorkflowFileStore.remove_if_exists, replacement_path)
            raise
        await db.refresh(workflow, attribute_names=['tags', 'updated_at'])
        return workflow

    @staticmethod
    async def delete(db: AsyncSession, user_id: UUID, workflow_id: UUID) -> None:
        workflow = await WorkflowService.get(db, user_id, workflow_id)
        if workflow.created_by_user_id != user_id:
            raise HTTPException(status_code=403, detail="Only the creator can delete this workflow.")
        workflow = await db.scalar(select(WorkflowModel).where(WorkflowModel.workflow_id == workflow_id).with_for_update().execution_options(populate_existing=True))
        if workflow is None or workflow.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Workflow not found.")
        await db.execute(update(WorkflowEmbeddingModel).where(
            WorkflowEmbeddingModel.workflow_id == workflow_id,
            WorkflowEmbeddingModel.status != "superseded"
        ).values(is_active=False, status="superseded"))
        workflow.resource_revision += 1
        workflow.search_revision += 1
        workflow.index_state = "not_indexed"
        # Soft Delete이므로 JSON 원본도 감사/복구를 위해 그대로 보존합니다.
        workflow.deleted_at = utc_now()
        await db.commit()

