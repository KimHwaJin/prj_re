"""Record cancellation requests without releasing a still-running execution."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.models.enums import TaskStatus
from api_service.models.agent_run_model import AgentRunModel
from api_service.models.session_model import SessionModel
from api_service.models.task_model import TaskModel
from api_service.runs.errors import RunConflict, RunNotFound
from api_service.utils import utc_now
from api_service.runs.task_events import TaskEventService
from api_service.runs.tasks import TaskService


async def cancel_task(
    db: AsyncSession, user_id: UUID, task_id: UUID, reason: str | None
) -> TaskModel:
    """전체 분석 Job을 취소합니다. waiting_input은 실행 coroutine이 없어 즉시 종료합니다."""
    task = await db.scalar(
        select(TaskModel)
        .join(SessionModel, SessionModel.session_id == TaskModel.session_id)
        .where(TaskModel.task_id == task_id, SessionModel.user_id == user_id)
    )
    if task is None:
        raise RunNotFound("Task not found.")
    latest_run = await db.scalar(
        select(AgentRunModel)
        .where(AgentRunModel.task_id == task_id)
        .order_by(AgentRunModel.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    # Same Run -> Task order as completion and recovery, avoiding an AB/BA
    # deadlock with a cancellation request during cleanup.
    task = await db.scalar(select(TaskModel).where(TaskModel.task_id == task_id)
                           .with_for_update().execution_options(populate_existing=True))
    if task is None:
        raise RunNotFound("Task not found.")
    current_run_id = await db.scalar(select(AgentRunModel.run_id).where(AgentRunModel.task_id == task_id)
                                     .order_by(AgentRunModel.created_at.desc()).limit(1))
    if current_run_id != (latest_run.run_id if latest_run is not None else None):
        raise RunConflict("Task execution changed; retry cancellation.")
    if task.status in TaskService.TERMINAL_STATUSES:
        raise RunConflict("Task is already terminal.")
    if task.recovery_required:
        raise RunConflict("Task requires recovery; termination has not been confirmed.")
    if latest_run is not None and task.status == TaskStatus.WAITING_INPUT and any(
        item.get("kind") == "EXECUTOR_EVENT" for item in (latest_run.interrupt or [])
    ):
        raise RunConflict("Executor cancellation is not supported; wait for its result.")
    if task.status == TaskStatus.WAITING_INPUT or latest_run is None:
        TaskService.transition(task, TaskStatus.CANCELED)
        task.cancel_requested_at = utc_now()
        if latest_run is not None:
            latest_run.cancel_reason = reason
            latest_run.cancel_requested_at = task.cancel_requested_at
            await TaskEventService.append(
                db, task_id=task_id, run_id=latest_run.run_id,
                event_type="task.canceled",
                payload={"status": TaskStatus.CANCELED.value, "reason": reason},
                commit=False,
            )
        await db.commit()
        await db.refresh(task)
        return task

    now = utc_now()
    task.cancel_requested_at = now
    latest_run.cancel_requested_at = now
    latest_run.cancel_reason = reason
    await db.commit()
    await db.refresh(task)
    return task
