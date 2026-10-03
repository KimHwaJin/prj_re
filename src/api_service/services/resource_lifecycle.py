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

from api_service.core.enums import AgentRunStatus, DeleteYN, LLMRunStatus, TaskStatus
from api_service.models.common.agent_command_model import AgentCommandModel
from api_service.models.common.agent_run_model import AgentRunModel
from api_service.models.common.llm_run_model import LLMRunModel
from api_service.models.common.project_model import ProjectModel
from api_service.models.common.session_model import SessionModel
from api_service.models.common.session_execution_model import SessionExecutionModel
from api_service.models.common.task_model import TaskModel
from api_service.repositories.user_repository import UserRepository

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
                       target_project_id: UUID | None = None,
                       for_update: bool = False) -> SessionModel:
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
    if for_update:
        query = query.with_for_update()
    session = await db.scalar(query.execution_options(populate_existing=True))
    if session is None:
        raise HTTPException(404, "Session not found.")
    # A move may have committed while discovering/locking the source project.
    # Do not acquire another project lock out of order or use stale context.
    if session.project_id != source or (expected_project_id is not None and session.project_id != expected_project_id):
        raise HTTPException(409, "Session project changed; refresh and retry.")
    return session


def unfinished_work_conditions(*, session_ids=None, session_id=None):
    """Shared predicates for mutation guards and read-only diagnostics.

    session_id can be an outer SQL column. Keep each inner table local so the
    EXISTS expressions also work when diagnostics join Task/owner themselves.
    """
    def matches(column):
        return column == session_id if session_id is not None else column.in_(session_ids)

    def exists(model, *conditions):
        return select(1).select_from(model).where(
            matches(model.session_id), *conditions,
        ).correlate_except(model).exists()

    return {
        "unfinished_command": exists(AgentCommandModel, AgentCommandModel.state.not_in(("DONE", "IGNORED", "FAILED"))),
        "unfinished_task": exists(TaskModel, or_(
            TaskModel.status.not_in(TERMINAL_TASKS), TaskModel.recovery_required.is_(True),
        )),
        "unfinished_run": exists(AgentRunModel, or_(
            AgentRunModel.status.in_((AgentRunStatus.PENDING, AgentRunStatus.RUNNING)),
            (AgentRunModel.status == AgentRunStatus.INTERRUPTED) & AgentRunModel.task_id.is_(None),
        )),
        "unfinished_llm": exists(LLMRunModel,
            LLMRunModel.status.in_((LLMRunStatus.QUEUED, LLMRunStatus.RUNNING))),
        "execution_held_or_uncertain": exists(SessionExecutionModel, or_(
            SessionExecutionModel.token.is_not(None), SessionExecutionModel.recovery_required.is_(True),
        )),
    }


async def require_idle(db: AsyncSession, session_ids, *, resource: str) -> None:
    """Caller owns admission/deletion barriers until mutation commits."""
    conditions = unfinished_work_conditions(session_ids=session_ids)
    if await db.scalar(select(or_(*conditions.values()))):
        raise HTTPException(409, f"{resource} has unfinished work; finish or cancel it before deletion or moving.")
