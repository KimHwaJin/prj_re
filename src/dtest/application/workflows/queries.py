from __future__ import annotations

from uuid import UUID
import asyncio

from dtest.contracts.errors import ApplicationError
from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from dtest.infrastructure.database.runtime import get_db
from dtest.contracts.pagination import ListParams
from dtest.infrastructure.database.pagination import fetch_page
from dtest.infrastructure.database.models.workflow_model import WorkflowModel, WorkflowTagModel
from dtest.contracts.resources.api_schema import Page
from dtest.contracts.resources.workflow_schema import (
    WorkflowCandidateCreate,
    WorkflowClone,
    WorkflowResource,
    WorkflowUpdate,
)
from dtest.infrastructure.file_storage.workflows import WorkflowFileStore
from dtest.application.workflows.service import WorkflowService
from dtest.infrastructure.workflow_search.runtime import get_workflow_runtime
from dtest.contracts.workflow_retrieval import WorkflowSearchRequest, WorkflowSearchResult


async def resource(workflow: WorkflowModel, *, include_document: bool = False) -> WorkflowResource:
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


async def indexed_resource(db, user_id, workflow):
    workflow_id, revision = workflow.workflow_id, workflow.search_revision
    needs_index = workflow.index_state == "pending"
    if needs_index:
        # Refresh after commit opened a read transaction; end it before HTTP.
        await db.rollback()
        await get_workflow_runtime().indexer.index(workflow_id, revision)
        workflow = await WorkflowService.get(db, user_id, workflow_id)
    return await resource(workflow, include_document=True)


async def list_workflows(db, user_id, params, q=None, lifecycle=None, tag=None):
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
    return {"items": [await resource(item) for item in items], "page": page}


async def reindex_workflow(db, user_id, workflow_id):
    workflow = await WorkflowService.get(db, user_id, workflow_id)
    if workflow.created_by_user_id != user_id:
        raise ApplicationError(status_code=403, detail="Only the creator can reindex this workflow.")
    revision = workflow.search_revision
    await db.rollback()
    await get_workflow_runtime().indexer.index(workflow_id, revision)
    return await resource(await WorkflowService.get(db, user_id, workflow_id), include_document=True)


async def search_workflows(query):
    return await get_workflow_runtime().search.search(query.strip())
