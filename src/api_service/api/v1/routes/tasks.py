"""Owner-scoped diagnostics. Commands and live state streams use Runs."""
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.core.auth import Actor, get_current_user_id, require_admin
from api_service.core.database import get_db
from api_service.core.pagination import ListParams, list_params
from api_service.schemas.common.api_schema import Page
from api_service.schemas.common.task_schema import TaskResource, TaskInvocationResource
from api_service.services import task_diagnostics as diagnostics

router = APIRouter(tags=["task-diagnostics"])
admin_router = APIRouter(prefix="/admin", tags=["admin-task-diagnostics"])


@router.get("/sessions/{session_id}/tasks", response_model=Page[TaskResource])
async def list_session_tasks(session_id: UUID, params: ListParams = Depends(list_params),
                             user_id: UUID = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    return await diagnostics.list_tasks(db, session_id, params, user_id=user_id)


@router.get("/tasks/{task_id}", response_model=TaskResource)
async def read_task(task_id: UUID, user_id: UUID = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    return await diagnostics.read_task(db, task_id, user_id=user_id)


@router.get("/tasks/{task_id}/runs", response_model=Page[TaskInvocationResource])
async def list_task_runs(task_id: UUID, params: ListParams = Depends(list_params),
                         user_id: UUID = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    return await diagnostics.list_invocations(db, task_id, params, user_id=user_id)


@admin_router.get("/sessions/{session_id}/tasks", response_model=Page[TaskResource])
async def admin_list_session_tasks(session_id: UUID, params: ListParams = Depends(list_params),
                                   actor: Actor = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Inspect any user's session, including soft-deleted resources. Read only."""
    return await diagnostics.list_tasks(db, session_id, params, user_id=None)


@admin_router.get("/tasks/{task_id}", response_model=TaskResource)
async def admin_read_task(task_id: UUID, actor: Actor = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return await diagnostics.read_task(db, task_id, user_id=None)


@admin_router.get("/tasks/{task_id}/runs", response_model=Page[TaskInvocationResource])
async def admin_list_task_runs(task_id: UUID, params: ListParams = Depends(list_params),
                               actor: Actor = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return await diagnostics.list_invocations(db, task_id, params, user_id=None)
