"""Short transaction barriers for admission versus CRUD lifecycle changes.

Order: user FOR SHARE -> sorted project advisory locks -> session admission
advisory lock -> optional Session row lock. Project readers share their barrier;
only project deletion is exclusive. Never hold these locks during graph I/O.
Workers' persistent Task/session-execution rows protect the execution interval.
"""
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AgentRunStatus, DeleteYN, LLMRunStatus, TaskStatus
from app.models.common.agent_run_model import AgentRunModel
from app.models.common.llm_run_model import LLMRunModel
from app.models.common.project_model import ProjectModel
from app.models.common.session_model import SessionModel
from app.models.common.session_execution_model import SessionExecutionModel
from app.models.common.task_model import TaskModel
from app.repositories.user_repository import UserRepository

TERMINAL_TASKS = (TaskStatus.SUCCESS, TaskStatus.ERROR, TaskStatus.TIMEOUT, TaskStatus.CANCELED)


async def lock_projects(db: AsyncSession, user_id: UUID, project_ids, *, exclusive=False):
    # Also protects service callers that did not enter through HTTP auth.
    if await UserRepository.get_active(db, user_id, for_share=True) is None:
        raise HTTPException(404, "User not found.")
    ids = sorted(set(project_ids), key=str)
    lock = func.pg_advisory_xact_lock if exclusive else func.pg_advisory_xact_lock_shared
    for project_id in ids:
        await db.execute(select(lock(func.hashtextextended(f"resource-project:{project_id}", 0))))
    projects = list(await db.scalars(select(ProjectModel).where(
        ProjectModel.project_id.in_(ids), ProjectModel.user_id == user_id,
        ProjectModel.delete_yn == DeleteYN.N,
    ).execution_options(populate_existing=True)))
    if len(projects) != len(ids):
        raise HTTPException(404, "Project not found.")
    return {project.project_id: project for project in projects}


async def lock_session(db: AsyncSession, user_id: UUID, session_id: UUID, *,
                       expected_project_id: UUID | None = None,
                       target_project_id: UUID | None = None) -> SessionModel:
    query = select(SessionModel).where(SessionModel.session_id == session_id,
        SessionModel.user_id == user_id, SessionModel.delete_yn == DeleteYN.N)
    session = await db.scalar(query.execution_options(populate_existing=True))
    if session is None:
        raise HTTPException(404, "Session not found.")
    source = session.project_id
    ids = [source] + ([target_project_id] if target_project_id is not None else [])
    await lock_projects(db, user_id, ids)
    # Same key as Run admission, shared across all API replicas.
    await db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(f"run-admission:{session_id}", 0))))
    session = await db.scalar(query.execution_options(populate_existing=True))
    if session is None:
        raise HTTPException(404, "Session not found.")
    # A move may have committed while discovering/locking the source project.
    # Do not acquire another project lock out of order or use stale context.
    if session.project_id != source or (expected_project_id is not None and session.project_id != expected_project_id):
        raise HTTPException(409, "Session project changed; refresh and retry.")
    return session


async def require_idle(db: AsyncSession, session_ids, *, resource: str) -> None:
    """Check one MVCC snapshot without waiting on Worker Run/Task row locks.

    Caller must own the admission barrier (or exclusive user/project deletion
    barrier) until mutation commits. A SELECT alone is not a race-safe guard.
    """
    busy_task = select(TaskModel.task_id).where(TaskModel.session_id.in_(session_ids), or_(
        TaskModel.status.not_in(TERMINAL_TASKS), TaskModel.recovery_required.is_(True),
    )).exists()
    busy_run = select(AgentRunModel.run_id).where(AgentRunModel.session_id.in_(session_ids), or_(
        AgentRunModel.status.in_((AgentRunStatus.PENDING, AgentRunStatus.RUNNING)),
        (AgentRunModel.status == AgentRunStatus.INTERRUPTED) & AgentRunModel.task_id.is_(None),
    )).exists()
    busy_llm = select(LLMRunModel.run_id).where(LLMRunModel.session_id.in_(session_ids),
        LLMRunModel.status.in_((LLMRunStatus.QUEUED, LLMRunStatus.RUNNING))).exists()
    busy_owner = select(SessionExecutionModel.session_id).where(
        SessionExecutionModel.session_id.in_(session_ids), or_(
            SessionExecutionModel.token.is_not(None), SessionExecutionModel.recovery_required.is_(True),
        )).exists()
    if await db.scalar(select(or_(busy_task, busy_run, busy_llm, busy_owner))):
        raise HTTPException(409, f"{resource} has unfinished work; finish or cancel it before deletion or moving.")
