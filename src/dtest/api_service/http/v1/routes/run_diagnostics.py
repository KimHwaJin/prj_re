"""Read-only diagnostics addressed through the stable public Run identity."""
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from dtest.api_service.http.dependencies import Actor, get_current_user_id, require_admin
from dtest.infrastructure.database.runtime import get_db
from dtest.api_service.http.pagination import ListParams, list_params
from dtest.contracts.resources.api_schema import Page
from dtest.contracts.resources.run_diagnostics_schema import RunDiagnosticsResource, RunInvocationResource
from dtest.application.runs import diagnostics

router = APIRouter(tags=["run-diagnostics"])
admin_router = APIRouter(prefix="/admin", tags=["admin-run-diagnostics"])


@router.get("/sessions/{session_id}/runs/{run_id}/diagnostics", response_model=RunDiagnosticsResource)
async def read_run_diagnostics(session_id: UUID, run_id: UUID,
                               user_id: UUID = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    """소유한 공개 Run의 내부 Task와 현재 세션 점유를 조사합니다. 조회로 실행 상태를 변경하지 않습니다."""
    return await diagnostics.read_diagnostics(db, session_id, run_id, user_id=user_id)


@router.get("/sessions/{session_id}/runs/{run_id}/invocations", response_model=Page[RunInvocationResource])
async def list_run_invocations(session_id: UUID, run_id: UUID, params: ListParams = Depends(list_params),
                               user_id: UUID = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    """최초 호출·resume 등 공개 Run에 속한 내부 실행 구간을 페이지로 조회합니다."""
    return await diagnostics.list_invocations(db, session_id, run_id, params, user_id=user_id)


@admin_router.get("/sessions/{session_id}/runs/{run_id}/diagnostics", response_model=RunDiagnosticsResource)
async def admin_read_run_diagnostics(session_id: UUID, run_id: UUID,
                                     actor: Actor = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """관리자가 타 사용자·소프트 삭제된 자원을 포함하여 조사합니다. 물리 삭제된 자원은404입니다."""
    return await diagnostics.read_diagnostics(db, session_id, run_id, user_id=None)


@admin_router.get("/sessions/{session_id}/runs/{run_id}/invocations", response_model=Page[RunInvocationResource])
async def admin_list_run_invocations(session_id: UUID, run_id: UUID, params: ListParams = Depends(list_params),
                                     actor: Actor = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """관리자가 타 사용자·소프트 삭제된 자원의 내부 실행 구간을 조회합니다."""
    return await diagnostics.list_invocations(db, session_id, run_id, params, user_id=None)
