import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dtest.settings.loader import get_settings
from dtest.settings.api import settings
from dtest.infrastructure.database.runtime import get_session_factory
from dtest.contracts.execution import ExecutionNeedsRecovery
from dtest.application.runs.lifecycle import finish_observer, wait_for_stop
from dtest.lifecycle import protected_cleanup
from dtest.application.runs.claim_context import ExecutionClaim, current_execution_claim
from dtest.contracts.enums import TaskStatus
from dtest.contracts.enums import AgentRunStatus
from dtest.infrastructure.database.models.agent_run_model import AgentRunModel
from dtest.infrastructure.database.models.task_model import TaskModel
from dtest.contracts.values import utc_now


class TaskService:
    @staticmethod
    def owns_execution(run: AgentRunModel, task: TaskModel | None, claim: ExecutionClaim) -> bool:
        return bool(
            task is not None and run.run_id == claim.run_id and run.task_id == claim.task_id
            and task.task_id == claim.task_id and task.lock_token == claim.lock_token
            and run.attempt_count == claim.attempt and run.status == AgentRunStatus.RUNNING
            and task.status == TaskStatus.RUNNING
        )

    @staticmethod
    def assert_execution_owner(run: AgentRunModel, task: TaskModel | None) -> None:
        claim = current_execution_claim.get()
        if claim is not None and (
            not TaskService.owns_execution(run, task, claim)
            or task.recovery_required or task.lease_expires_at is None or task.lease_expires_at <= utc_now()
        ):
            raise ExecutionNeedsRecovery("Execution claim is stale or expired.")

    @staticmethod
    async def attach_graph_task_for_run(
        db: AsyncSession,
        *,
        run_id: UUID,
        graph_task_id: UUID,
    ) -> None:
        """Link the graph/Executor task identity to its durable CRUD Task."""

        task = await db.scalar(select(TaskModel).join(
            AgentRunModel, AgentRunModel.task_id == TaskModel.task_id,
        ).where(AgentRunModel.run_id == run_id))
        if task is None or task.graph_task_id == graph_task_id:
            return
        if task.graph_task_id is not None and task.graph_task_id != graph_task_id:
            raise RuntimeError(
                "CRUD Task is already linked to a different graph_task_id: "
                f"task_id={task.task_id}, current={task.graph_task_id}, "
                f"incoming={graph_task_id}"
            )
        task.graph_task_id = graph_task_id
        await db.commit()

    """tasks 테이블 CRUD와 E03 Session lock lifecycle만 담당합니다."""

    ACTIVE_STATUSES = {TaskStatus.PENDING, TaskStatus.RUNNING}
    TERMINAL_STATUSES = {TaskStatus.SUCCESS, TaskStatus.ERROR, TaskStatus.TIMEOUT, TaskStatus.CANCELED}

    @staticmethod
    def retry_delay_seconds(attempt_count: int) -> float:
        """종료가 확인된 Graph 예외의 재시도 backoff입니다. 소유권 불명 실행에는 적용하지 않습니다."""
        base = max(0.0, get_settings().commands.agent_worker_retry_backoff_seconds)
        ceiling = max(base, get_settings().commands.agent_worker_retry_max_backoff_seconds)
        return min(ceiling, base * (2 ** max(0, attempt_count - 1)))

    @staticmethod
    def create_model(*, session_id: UUID, idempotency_key: str, owner: str) -> TaskModel:
        now = utc_now()
        return TaskModel(
            session_id=session_id,
            idempotency_key=idempotency_key,
            lock_owner=owner,
            lock_token=uuid4(),
            heartbeat_at=now,
            lease_expires_at=now + timedelta(seconds=get_settings().commands.task_lease_seconds),
        )

    @staticmethod
    def transition(task: TaskModel, status: TaskStatus, *, failure_reason: str | None = None) -> None:
        """Task 생애주기와 실행 lease를 함께 전환합니다.

        waiting_input은 Job이 살아 있는 상태지만 실행 중이 아니므로 Session lease만 풉니다.
        """
        if task.recovery_required:
            raise ExecutionNeedsRecovery("Task requires recovery before transition.")
        task.status = status
        task.failure_reason = failure_reason
        if status in TaskService.TERMINAL_STATUSES:
            task.completed_at = utc_now()
        if status not in TaskService.ACTIVE_STATUSES:
            task.lock_owner = task.lock_token = task.heartbeat_at = task.lease_expires_at = None

    @staticmethod
    def start_execution(task: TaskModel, *, owner: str) -> UUID:
        """waiting_input Task를 새 Run이 실행할 수 있도록 원자 잠금 상태로 되돌립니다."""
        if task.recovery_required:
            raise ExecutionNeedsRecovery("Task requires recovery before execution.")
        now = utc_now()
        token = uuid4()
        task.status = TaskStatus.RUNNING
        task.lock_owner = owner
        task.lock_token = token
        task.heartbeat_at = now
        task.lease_expires_at = now + timedelta(seconds=get_settings().commands.task_lease_seconds)
        task.failure_reason = None
        task.completed_at = None
        return token

    @staticmethod
    async def attach_trigger(db: AsyncSession, *, task_id: UUID, message_id: UUID, commit: bool = True) -> None:
        """E03-T02: Graph가 저장한 원본 사용자 Message를 분석 Task에 연결합니다."""
        await db.execute(update(TaskModel).where(TaskModel.task_id == task_id).values(trigger_message_id=message_id))
        if commit:
            await db.commit()

    @staticmethod
    async def heartbeat(db: AsyncSession, *, task_id: UUID, lock_token: UUID) -> bool:
        now = utc_now()
        result = await db.execute(
            update(TaskModel).where(
                TaskModel.task_id == task_id,
                TaskModel.lock_token == lock_token,
                TaskModel.recovery_required.is_(False),
                TaskModel.status.in_(TaskService.ACTIVE_STATUSES),
            ).values(heartbeat_at=now, lease_expires_at=now + timedelta(seconds=get_settings().commands.task_lease_seconds))
        )
        await db.commit()
        return bool(result.rowcount)

    @staticmethod
    @asynccontextmanager
    async def lease_heartbeat(task_id: UUID, lock_token: UUID, *, run_id: UUID):
        stop = asyncio.Event()

        async def loop() -> None:
            while not stop.is_set():
                await wait_for_stop(stop, max(1, get_settings().commands.task_lease_seconds // 3))
                if stop.is_set():
                    return
                async with asyncio.timeout(get_settings().commands.run_monitor_timeout_seconds):
                    async with get_session_factory()() as heartbeat_db:
                        if not await TaskService.heartbeat(heartbeat_db, task_id=task_id, lock_token=lock_token):
                            raise ExecutionNeedsRecovery("Execution lease is no longer owned.")

        heartbeat_task = asyncio.create_task(loop(), name=f"lease-heartbeat:{run_id}")
        try:
            yield heartbeat_task
        finally:
            stop.set()
            await protected_cleanup(finish_observer(heartbeat_task, run_id=run_id, stage="heartbeat_stop"))

    @staticmethod
    def _mark_recovery(task: TaskModel, run: AgentRunModel | None, reason: str) -> None:
        # Preserve the active status/session lock until the old writer is proven
        # stopped. A lease timeout alone is not a checkpoint fencing mechanism.
        if not task.recovery_required:
            task.recovery_required = True
            task.failure_reason = f"Execution requires recovery: {reason}"
        if run is not None and (run.failure or {}).get("code") != "RUN_RECOVERY_REQUIRED":
            run.failure = {"code": "RUN_RECOVERY_REQUIRED", "message": reason, "retry_scheduled": False}
            run.next_attempt_at = None

    @staticmethod
    async def require_recovery(db: AsyncSession, *, run_id: UUID, reason: str) -> bool:
        # Match completion/claim lock order: Run -> Task.
        run = await db.scalar(select(AgentRunModel).where(AgentRunModel.run_id == run_id)
                              .with_for_update().execution_options(populate_existing=True))
        if run is None or run.status != AgentRunStatus.RUNNING:
            await db.rollback()
            return False
        task = await db.scalar(select(TaskModel).where(TaskModel.task_id == run.task_id)
                               .with_for_update().execution_options(populate_existing=True))
        if task is None or task.status != TaskStatus.RUNNING:
            await db.rollback()
            return False
        claim = current_execution_claim.get()
        if claim is not None and not TaskService.owns_execution(run, task, claim):
            # A delayed old writer/recorder cannot quarantine a newer owner.
            await db.rollback()
            return False
        TaskService._mark_recovery(task, run, reason)
        await db.commit()
        return True

    @staticmethod
    async def reconcile_stale(db: AsyncSession, *, now: datetime | None = None, batch_size: int = 100) -> list[UUID]:
        cutoff = now or utc_now()
        stale = (
            TaskModel.status == TaskStatus.RUNNING,
            TaskModel.recovery_required.is_(False),
            TaskModel.lease_expires_at.is_not(None),
            TaskModel.lease_expires_at < cutoff,
        )
        # Queue waiting time is not an execution lease. Never expire PENDING.
        runs = list((await db.scalars(
            select(AgentRunModel).join(TaskModel, TaskModel.task_id == AgentRunModel.task_id)
            .where(AgentRunModel.status == AgentRunStatus.RUNNING, *stale)
            .order_by(TaskModel.lease_expires_at).limit(batch_size)
            .with_for_update(of=AgentRunModel, skip_locked=True)
        )).all())
        affected = []
        for run in runs:
            task = await db.scalar(select(TaskModel).where(TaskModel.task_id == run.task_id)
                                   .with_for_update().execution_options(populate_existing=True))
            # Heartbeat can extend the lease while we acquire the Task row lock.
            if (task.status == TaskStatus.RUNNING and not task.recovery_required
                    and task.lease_expires_at is not None and task.lease_expires_at < cutoff):
                TaskService._mark_recovery(task, run, "lease_expired_owner_unconfirmed")
                affected.append(task.task_id)
        # Historical orphan Tasks must also retain their session lock. There is
        # no running Run to acquire first for these rows.
        orphaned = list((await db.scalars(
            select(TaskModel).where(*stale, ~select(AgentRunModel.run_id).where(
                AgentRunModel.task_id == TaskModel.task_id,
                AgentRunModel.status == AgentRunStatus.RUNNING,
            ).exists()).order_by(TaskModel.lease_expires_at)
            .limit(max(0, batch_size - len(runs))).with_for_update(skip_locked=True)
        )).all())
        for task in orphaned:
            TaskService._mark_recovery(task, None, "lease_expired_without_running_run")
            affected.append(task.task_id)
        await db.commit()
        return affected
