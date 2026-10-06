from __future__ import annotations
from dtest.application.workflows import queries

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from dtest.api_service.http.dependencies import get_current_user_id
from dtest.infrastructure.database.runtime import get_db
from dtest.contracts.pagination import ListParams
from dtest.api_service.http.pagination import list_params
from dtest.contracts.resources.api_schema import Page
from dtest.contracts.resources.workflow_schema import (
    WorkflowCandidateCreate,
    WorkflowClone,
    WorkflowResource,
    WorkflowUpdate,
)
from dtest.application.workflows.service import WorkflowService
from dtest.contracts.workflow_retrieval import WorkflowSearchRequest, WorkflowSearchResult


router = APIRouter(prefix="/workflows", tags=["workflows"])


@router.post("", response_model=WorkflowResource, status_code=status.HTTP_201_CREATED)
async def create_candidate(payload: WorkflowCandidateCreate, user_id: UUID = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    return await queries.indexed_resource(db, user_id, await WorkflowService.create_candidate(db, user_id, payload))


@router.get("", response_model=Page[WorkflowResource])
async def list_workflows(
    q: str | None = Query(default=None, max_length=200),
    lifecycle: str | None = Query(default=None, pattern="^(candidate|template)$"),
    tag: str | None = Query(default=None, max_length=50),
    params: ListParams = Depends(list_params),
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await queries.list_workflows(db, user_id, params, q, lifecycle, tag)


@router.post("/search", response_model=WorkflowSearchResult)
async def search_workflows(payload: WorkflowSearchRequest, user_id: UUID = Depends(get_current_user_id)):
    # Authenticated global templates only. Candidate ownership is not a filter:
    # unpublished candidates never enter the active search projection.
    return await queries.search_workflows(payload.query)


@router.post("/{workflow_id}/reindex", response_model=WorkflowResource)
async def reindex_workflow(workflow_id: UUID, user_id: UUID = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    return await queries.reindex_workflow(db, user_id, workflow_id)


@router.get("/{workflow_id}", response_model=WorkflowResource)
async def read_workflow(workflow_id: UUID, user_id: UUID = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    return await queries.resource(await WorkflowService.get(db, user_id, workflow_id), include_document=True)


@router.patch("/{workflow_id}", response_model=WorkflowResource)
async def update_workflow(workflow_id: UUID, payload: WorkflowUpdate, user_id: UUID = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    return await queries.indexed_resource(db, user_id, await WorkflowService.update(db, user_id, workflow_id, payload))


@router.post("/{workflow_id}/promote", response_model=WorkflowResource, status_code=status.HTTP_201_CREATED)
async def promote_workflow(workflow_id: UUID, user_id: UUID = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    return await queries.indexed_resource(db, user_id, await WorkflowService.promote(db, user_id, workflow_id))


@router.post("/{workflow_id}/clone", response_model=WorkflowResource, status_code=status.HTTP_201_CREATED)
async def clone_workflow(workflow_id: UUID, payload: WorkflowClone, user_id: UUID = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    return await queries.indexed_resource(db, user_id, await WorkflowService.clone(db, user_id, workflow_id, payload))


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(workflow_id: UUID, user_id: UUID = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    await WorkflowService.delete(db, user_id, workflow_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
