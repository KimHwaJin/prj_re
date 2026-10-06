from __future__ import annotations

from uuid import UUID
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api_service.api.dependencies import get_current_user_id
from api_service.infrastructure.database import get_db
from api_service.api.pagination import ListParams, fetch_page, list_params
from api_service.models.workflow_model import WorkflowModel, WorkflowTagModel
from api_service.schemas.api_schema import Page
from api_service.schemas.workflow_schema import (
    WorkflowCandidateCreate,
    WorkflowClone,
    WorkflowResource,
    WorkflowUpdate,
)
from api_service.workflows.file_store import WorkflowFileStore
from api_service.workflows.service import WorkflowService
from api_service.workflows.search.runtime import get_workflow_runtime
from service_contracts.workflow_retrieval import WorkflowSearchRequest, WorkflowSearchResult


router = APIRouter(prefix="/workflows", tags=["workflows"])


async def _resource(workflow: WorkflowModel, *, include_document: bool = False) -> WorkflowResource:
    return WorkflowResource(
        workflow_id=workflow.workflow_id,
        name=workflow.name,
        description=workflow.description,
        goal=workflow.goal,
        schema_version=workflow.schema_version,
        lifecycle=workflow.lifecycle,
        file_path=workflow.file_path,
        content_sha256=workflow.content_sha256,
        user_queries=workflow.user_queries, resource_revision=workflow.resource_revision,
        search_revision=workflow.search_revision, index_state=workflow.index_state, index_error=workflow.index_error,
        source_run_id=workflow.source_run_id,
        source_workflow_id=workflow.source_workflow_id,
        created_by_user_id=workflow.created_by_user_id,
        is_recommendable=workflow.is_recommendable,
        tags=[item.tag for item in workflow.tags],
        document=await asyncio.to_thread(WorkflowFileStore.read, workflow.file_path) if include_document else None,
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
        deleted_at=workflow.deleted_at,
    )


@router.post("", response_model=WorkflowResource, status_code=status.HTTP_201_CREATED)
async def create_candidate(payload: WorkflowCandidateCreate, user_id: UUID = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    return await _indexed_resource(db, user_id, await WorkflowService.create_candidate(db, user_id, payload))


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
    return {"items": [await _resource(item) for item in items], "page": page}


@router.post("/search", response_model=WorkflowSearchResult)
async def search_workflows(payload: WorkflowSearchRequest, user_id: UUID = Depends(get_current_user_id)):
    # Authenticated global templates only. Candidate ownership is not a filter:
    # unpublished candidates never enter the active search projection.
    return await get_workflow_runtime().search.search(payload.query.strip())


async def _indexed_resource(db, user_id, workflow):
    workflow_id, revision = workflow.workflow_id, workflow.search_revision
    needs_index = workflow.index_state == "pending"
    if needs_index:
        # Refresh after commit opened a read transaction; end it before HTTP.
        await db.rollback()
        await get_workflow_runtime().indexer.index(workflow_id, revision)
        workflow = await WorkflowService.get(db, user_id, workflow_id)
    return await _resource(workflow, include_document=True)


@router.post("/{workflow_id}/reindex", response_model=WorkflowResource)
async def reindex_workflow(workflow_id: UUID, user_id: UUID = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    workflow = await WorkflowService.get(db, user_id, workflow_id)
    if workflow.created_by_user_id != user_id:
        raise HTTPException(status_code=403, detail="Only the creator can reindex this workflow.")
    revision = workflow.search_revision
    await db.rollback()
    await get_workflow_runtime().indexer.index(workflow_id, revision)
    return await _resource(await WorkflowService.get(db, user_id, workflow_id), include_document=True)


@router.get("/{workflow_id}", response_model=WorkflowResource)
async def read_workflow(workflow_id: UUID, user_id: UUID = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    return await _resource(await WorkflowService.get(db, user_id, workflow_id), include_document=True)


@router.patch("/{workflow_id}", response_model=WorkflowResource)
async def update_workflow(workflow_id: UUID, payload: WorkflowUpdate, user_id: UUID = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    return await _indexed_resource(db, user_id, await WorkflowService.update(db, user_id, workflow_id, payload))


@router.post("/{workflow_id}/promote", response_model=WorkflowResource, status_code=status.HTTP_201_CREATED)
async def promote_workflow(workflow_id: UUID, user_id: UUID = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    return await _indexed_resource(db, user_id, await WorkflowService.promote(db, user_id, workflow_id))


@router.post("/{workflow_id}/clone", response_model=WorkflowResource, status_code=status.HTTP_201_CREATED)
async def clone_workflow(workflow_id: UUID, payload: WorkflowClone, user_id: UUID = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    return await _indexed_resource(db, user_id, await WorkflowService.clone(db, user_id, workflow_id, payload))


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(workflow_id: UUID, user_id: UUID = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    await WorkflowService.delete(db, user_id, workflow_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

