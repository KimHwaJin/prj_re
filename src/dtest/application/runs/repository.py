"""Run queries and Run-to-Task locking order at the database boundary."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dtest.contracts.enums import AgentRunStatus
from dtest.infrastructure.database.models.agent_run_model import AgentRunModel
from dtest.infrastructure.database.models.task_model import TaskModel
from dtest.infrastructure.database.repositories.session_repository import (
    SessionRepository,
)
from dtest.application.runs.errors import RunConflict, RunNotFound
from dtest.application.runs.tasks import TaskService
from dtest.contracts.execution import ExecutionNeedsRecovery
from dtest.infrastructure.observability.diagnostics import timed


@timed("run.lock_final_rows")
async def lock_run_and_task(
    db: AsyncSession,
    run_id: UUID,
) -> tuple[AgentRunModel, TaskModel | None]:
    """완료와 취소가 서로 덮어쓰지 않도록 같은 transaction에서 행을 잠급니다."""
    run = await db.scalar(
        select(AgentRunModel)
        .where(AgentRunModel.run_id == run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if run is None:
        raise RuntimeError(f"Run disappeared during execution: {run_id}")
    task = await db.scalar(
        select(TaskModel)
        .where(TaskModel.task_id == run.task_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if task is not None and task.recovery_required:
        raise ExecutionNeedsRecovery(
            "Task requires recovery; late completion is rejected."
        )
    TaskService.assert_execution_owner(run, task)
    return run, task


async def require_session(db: AsyncSession, user_id: UUID, session_id: UUID):
    session = await SessionRepository.get_active_by_user(
        db, user_id=user_id, session_id=session_id
    )
    if session is None:
        raise RunNotFound("Session not found.")
    return session


async def interrupted_run(
    db: AsyncSession, session_id: UUID, resume_run_id: UUID | None
) -> AgentRunModel:
    conditions = [AgentRunModel.session_id == session_id]
    if resume_run_id is not None:
        conditions.append(AgentRunModel.run_id == resume_run_id)
    else:
        conditions.append(AgentRunModel.status == AgentRunStatus.INTERRUPTED)
    run = await db.scalar(
        select(AgentRunModel)
        .where(*conditions)
        .order_by(AgentRunModel.created_at.desc())
    )
    if run is None or run.status != AgentRunStatus.INTERRUPTED:
        raise RunConflict(
            "No interrupted run exists to resume in this session."
        )
    return run


async def attach_trigger_message(
    db: AsyncSession, *, run_id: UUID, message_id: UUID, commit: bool = True
) -> None:
    run = await db.get(AgentRunModel, run_id)
    if run is not None and run.trigger_message_id is None:
        run.trigger_message_id = message_id
        if run.task_id is not None:
            await TaskService.attach_trigger(
                db, task_id=run.task_id, message_id=message_id, commit=commit
            )
