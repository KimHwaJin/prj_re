"""Public lifecycle projection over durable, private invocation records.

No second state machine: one SQL snapshot combines the latest invocation and
Task. Workers still claim invocation IDs; checkpoints keep their original IDs.
"""
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import aliased
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AgentRunStatus
from app.models.common.agent_run_model import AgentRunModel as Run
from app.models.common.task_model import TaskModel as Task
from app.schemas.common.run_schema import PublicRunResource, RunCreate, RunResume, RunStart, RunCancel
from app.services.run_service import RunService
from app.services.task_service import TaskService
from app.services.helpers import utc_now
from app.services import resource_lifecycle as lifecycle


TERMINAL = {"success", "error", "timeout", "canceled"}


def project(root: Run, latest: Run, task: Task | None) -> PublicRunResource:
    status = latest.status.value
    if task and task.status in TaskService.TERMINAL_STATUSES:
        status = task.status.value
    elif status == AgentRunStatus.INTERRUPTED:
        status = "waiting_executor" if any(
            item.get("kind") == "EXECUTOR_EVENT" for item in (latest.interrupt or [])
        ) else "waiting_input"
    recovery = bool(task and task.recovery_required)
    if recovery:
        status = "recovery_required"
    terminal = status in TERMINAL
    times = [root.updated_at, latest.updated_at] + ([task.updated_at] if task else [])
    return PublicRunResource(
        id=root.run_id, session_id=root.session_id, status=status,
        resume_token=latest.run_id if status == "waiting_input" and not (task and task.cancel_requested_at) else None,
        interrupt=latest.interrupt if status in {"waiting_input", "waiting_executor"} else None,
        failure=latest.failure, result=latest.agent_response if terminal else None,
        recovery_required=recovery, checkpoint_run_id=root.checkpoint_run_id,
        task_id=latest.task_id, attempt_count=latest.attempt_count,
        next_attempt_at=latest.next_attempt_at, cancel_reason=latest.cancel_reason,
        cancel_requested_at=task.cancel_requested_at if task else latest.cancel_requested_at,
        created_at=root.created_at, updated_at=max(times), started_at=root.started_at,
        completed_at=(task.completed_at if task else latest.completed_at) if terminal else None,
    )


class PublicRunService:
    @staticmethod
    async def admission_lock(db: AsyncSession, user_id: UUID, session_id: UUID) -> None:
        await lifecycle.lock_session(db, user_id, session_id)

    @staticmethod
    async def snapshots(db: AsyncSession, ids: list[UUID]):
        root, latest = aliased(Run), aliased(Run)
        latest_id = (select(Run.run_id).where(Run.public_run_id == root.run_id)
                     .order_by(Run.created_at.desc(), Run.run_id.desc()).limit(1).correlate(root).scalar_subquery())
        result = await db.execute(select(root, latest, Task).select_from(root)
            .join(latest, latest.run_id == latest_id).outerjoin(Task, Task.task_id == latest.task_id)
            .where(root.run_id.in_(ids), root.public_run_id == root.run_id)
            .execution_options(populate_existing=True))
        return {r.run_id: (r, invocation, task) for r, invocation, task in result.all()}

    @staticmethod
    async def read(db: AsyncSession, user_id: UUID, session_id: UUID, run_id: UUID) -> PublicRunResource:
        await RunService._session(db, user_id, session_id)
        # Old invocation URLs remain read aliases, but always return canonical id.
        canonical = await db.scalar(select(Run.public_run_id).where(Run.run_id == run_id, Run.session_id == session_id))
        if canonical is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        snapshots = await PublicRunService.snapshots(db, [canonical])
        if canonical not in snapshots or snapshots[canonical][0].session_id != session_id:
            raise HTTPException(status_code=404, detail="Run not found.")
        return project(*snapshots[canonical])

    @staticmethod
    async def create(db: AsyncSession, user_id: UUID, session_id: UUID, payload: RunStart, key: str) -> PublicRunResource:
        payload = RunCreate(**payload.model_dump())
        reserved = {"resume_run_id", "checkpoint_run_id", "task_id", "_request_digest", "_public_resume"}
        if reserved.intersection(payload.metadata):
            raise HTTPException(status_code=422, detail="Execution identity metadata is managed by the server.")
        invocation = await RunService.create(db, user_id, session_id, payload, key)
        return await PublicRunService.read(db, user_id, session_id, invocation.public_run_id)

    @staticmethod
    async def resume(db: AsyncSession, user_id: UUID, session_id: UUID, run_id: UUID, payload: RunResume, key: str) -> PublicRunResource:
        await RunService._session(db, user_id, session_id)
        await PublicRunService.admission_lock(db, user_id, session_id)
        current = await PublicRunService.read(db, user_id, session_id, run_id)
        command = RunCreate(command=payload.command, metadata={
            "resume_run_id": str(payload.resume_token), "_public_resume": str(current.id),
        })
        previous = await db.scalar(select(Run).where(Run.session_id == session_id, Run.idempotency_key == key))
        if previous is not None:
            if previous.public_run_id != current.id:
                raise HTTPException(status_code=409, detail="Idempotency-Key belongs to another Run.")
            RunService.validate_replay(previous, command)
            return current
        if current.status != "waiting_input" or current.resume_token != payload.resume_token:
            raise HTTPException(status_code=409, detail="Run is not waiting for this input; refresh Run state.")
        await RunService.create(db, user_id, session_id, command, key)
        return await PublicRunService.read(db, user_id, session_id, current.id)

    @staticmethod
    async def cancel(db: AsyncSession, user_id: UUID, session_id: UUID, run_id: UUID, payload: RunCancel) -> PublicRunResource:
        await RunService._session(db, user_id, session_id)
        await PublicRunService.admission_lock(db, user_id, session_id)
        current = await PublicRunService.read(db, user_id, session_id, run_id)
        if current.status in TERMINAL:
            if current.status == "canceled":
                return current
            raise HTTPException(status_code=409, detail="Run is already terminal.")
        if current.status == "waiting_executor":
            # We have no confirmed remote cancellation protocol here. Never
            # unlock a week-long external job by merely canceling the local Task.
            raise HTTPException(status_code=409, detail="Executor cancellation is not supported; the Run remains locked until its result.")
        if current.task_id:
            await RunService.cancel_task(db, user_id, current.task_id, payload.reason)
        else:
            snapshots = await PublicRunService.snapshots(db, [current.id])
            latest = await db.scalar(select(Run).where(Run.run_id == snapshots[current.id][1].run_id)
                                     .with_for_update().execution_options(populate_existing=True))
            if latest.status != AgentRunStatus.INTERRUPTED:
                raise HTTPException(status_code=409, detail="Legacy Run has no Task; execution termination requires recovery.")
            # Historical FAQ interrupts have no Worker to consume a cancel flag.
            # The paused invocation is finished, so close only its public history.
            latest.cancel_requested_at = utc_now()
            latest.cancel_reason = payload.reason
            RunService._finish_run(latest, AgentRunStatus.CANCELED)
            await db.commit()
        return await PublicRunService.read(db, user_id, session_id, current.id)
