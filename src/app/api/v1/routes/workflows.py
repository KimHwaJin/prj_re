from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.auth import get_current_user_id
from app.core.database import get_db
from app.core.pagination import ListParams, fetch_page, list_params
from app.models.common.workflow_model import WorkflowModel, WorkflowTagModel
from app.schemas.common.api_schema import Page
from app.schemas.common.workflow_schema import (
    WorkflowCandidateCreate,
    WorkflowClone,
    WorkflowResource,
    WorkflowUpdate,
)
from app.services.workflow_file_store import WorkflowFileStore
from app.services.workflow_service import WorkflowService


router = APIRouter(prefix="/workflows", tags=["workflows"])


def _resource(workflow: WorkflowModel, *, include_document: bool = False) -> WorkflowResource:
    return WorkflowResource(
        workflow_id=workflow.workflow_id,
        name=workflow.name,
        description=workflow.description,
        goal=workflow.goal,
        schema_version=workflow.schema_version,
        lifecycle=workflow.lifecycle,
        file_path=workflow.file_path,
        content_sha256=workflow.content_sha256,
        source_run_id=workflow.source_run_id,
        source_workflow_id=workflow.source_workflow_id,
        created_by_user_id=workflow.created_by_user_id,
        is_recommendable=workflow.is_recommendable,
        tags=[item.tag for item in workflow.tags],
        document=WorkflowFileStore.read(workflow.file_path) if include_document else None,
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
        deleted_at=workflow.deleted_at,
    )


@router.post("", response_model=WorkflowResource, status_code=status.HTTP_201_CREATED)
async def create_candidate(payload: WorkflowCandidateCreate, user_id: UUID = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    return _resource(await WorkflowService.create_candidate(db, user_id, payload), include_document=True)


@router.get("", response_model=Page[WorkflowResource])
async def list_workflows(
    q: str | None = Query(default=None, max_length=200),
    lifecycle: str | None = Query(default=None, pattern="^(candidate|template)$"),
    tag: str | None = Query(default=None, max_length=50),
    params: ListParams = Depends(list_params),
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(WorkflowModel).options(selectinload(WorkflowModel.tags)).where(
        WorkflowModel.deleted_at.is_(None), WorkflowService._visibility(user_id)
    )
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(or_(WorkflowModel.name.ilike(pattern), WorkflowModel.description.ilike(pattern)))
    if lifecycle:
        stmt = stmt.where(WorkflowModel.lifecycle == lifecycle)
    if tag:
        stmt = stmt.where(exists().where(WorkflowTagModel.workflow_id == WorkflowModel.workflow_id, WorkflowTagModel.tag == tag.strip().lower()))
    items, page = await fetch_page(db, stmt, model=WorkflowModel, id_name="workflow_id", params=params)
    return {"items": [_resource(item) for item in items], "page": page}


@router.get("/{workflow_id}", response_model=WorkflowResource)
async def read_workflow(workflow_id: UUID, user_id: UUID = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    return _resource(await WorkflowService.get(db, user_id, workflow_id), include_document=True)


@router.patch("/{workflow_id}", response_model=WorkflowResource)
async def update_workflow(workflow_id: UUID, payload: WorkflowUpdate, user_id: UUID = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    return _resource(await WorkflowService.update(db, user_id, workflow_id, payload))


@router.post("/{workflow_id}/promote", response_model=WorkflowResource, status_code=status.HTTP_201_CREATED)
async def promote_workflow(workflow_id: UUID, user_id: UUID = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    return _resource(await WorkflowService.promote(db, user_id, workflow_id), include_document=True)


@router.post("/{workflow_id}/clone", response_model=WorkflowResource, status_code=status.HTTP_201_CREATED)
async def clone_workflow(workflow_id: UUID, payload: WorkflowClone, user_id: UUID = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    return _resource(await WorkflowService.clone(db, user_id, workflow_id, payload), include_document=True)


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(workflow_id: UUID, user_id: UUID = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    await WorkflowService.delete(db, user_id, workflow_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

