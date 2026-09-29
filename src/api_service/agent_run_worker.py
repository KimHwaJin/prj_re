"""Gaia Router lifespan에서 실행되는 PostgreSQL 기반 Agent Run Worker."""

from __future__ import annotations

import asyncio
import logging
import socket
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import or_, select

from config import settings
from api_service.core.database import get_session_factory
from service_contracts.execution import ExecutionNeedsRecovery
from api_service.core.execution_lifecycle import execution_health
from service_runtime.cleanup import protected_cleanup
from api_service.core.execution_claim import ExecutionClaim, bind_execution_claim
from api_service.core.enums import AgentRunStatus, TaskStatus
from api_service.models.common.agent_run_model import AgentRunModel
from api_service.models.common.task_model import TaskModel
from api_service.models.common.session_execution_model import SessionExecutionModel
from api_service.services.session_execution import SessionExecution, acquire, run_owned
from api_service.schemas.common.run_schema import RunCreate
from api_service.services.run_service import RunService
from api_service.services.task_service import TaskService
from api_service.services.helpers import utc_now
from service_runtime.diagnostics import run_trace, span

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClaimedRun:
    claim: ExecutionClaim
    user_id: UUID
    session_id: UUID
    payload: RunCreate
    key: str


async def claim_one() -> ClaimedRun | None:
    """pending Run 하나를 원자 점유하고 실행에 필요한 immutable 값만 반환합니다."""

    if not execution_health.healthy:
        raise ExecutionNeedsRecovery("Worker is unhealthy; claiming another Run is prohibited.")
    async with get_session_factory()() as db:
        run = await db.scalar(
            select(AgentRunModel)
            .where(
                AgentRunModel.status == AgentRunStatus.PENDING,
                ~select(SessionExecutionModel.session_id).where(
                    SessionExecutionModel.session_id == AgentRunModel.session_id,
                    or_(SessionExecutionModel.token.is_not(None),
                        SessionExecutionModel.recovery_required.is_(True)),
                ).exists(),
                ~select(TaskModel.task_id).where(
                    TaskModel.task_id == AgentRunModel.task_id,
                    TaskModel.recovery_required.is_(True),
                ).exists(),
                or_(
                    AgentRunModel.next_attempt_at.is_(None),
                    AgentRunModel.next_attempt_at <= utc_now(),
                ),
            )
            .order_by(AgentRunModel.created_at, AgentRunModel.run_id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if run is None:
            return None
        task = await db.scalar(
            select(TaskModel)
            .where(TaskModel.task_id == run.task_id)
            .with_for_update()
        )
        if task is None or task.status != TaskStatus.PENDING:
            # 손상된 queue row를 무한 재조회하지 않도록 명시적으로 실패시킵니다.
            run.status = AgentRunStatus.ERROR
            run.failure = {"message": "Queued Run has no pending Task."}
            await db.commit()
            return None

        metadata = dict(run.metadata_json or {})
        user_id = UUID(str(metadata["requested_by_user_id"]))
        owner = f"gaia:{socket.gethostname()}"
        TaskService.start_execution(task, owner=owner)
        if not await acquire(db, SessionExecution(run.session_id, task.lock_token, run.run_id, 'api_run')):
            await db.rollback()
            return None
        run.status = AgentRunStatus.RUNNING
        # row lock 안에서 증가하므로 여러 Pod가 같은 attempt 번호를 가질 수 없습니다.
        run.attempt_count += 1
        run.next_attempt_at = None
        run.started_at = utc_now()
        execution_claim = ExecutionClaim(run.run_id, task.task_id, task.lock_token, run.attempt_count)
        await db.commit()
        payload = RunCreate(
            input=run.input_json,
            command=run.command_json,
            metadata=metadata,
            multitask_strategy=run.multitask_strategy,
            stream_mode=run.stream_mode,
            stream_resumable=run.stream_resumable,
            on_disconnect=run.on_disconnect,
        )
        return ClaimedRun(
            execution_claim,
            user_id, run.session_id, payload, run.idempotency_key,
        )


async def execute_claimed(item: ClaimedRun) -> None:
    """요청 DB session과 분리된 session에서 이미 점유한 Graph Run을 실행합니다."""

    with bind_execution_claim(item.claim):
        try:
            await run_owned(
                SessionExecution(item.session_id, item.claim.lock_token, item.claim.run_id, 'api_run'),
                lambda: _execute_claimed(item),
            )
        except asyncio.CancelledError:
            # Covers cancellation before RunService has installed its guards too.
            execution_health.fail(item.claim.run_id, "worker_cancelled")
            raise


async def _execute_claimed(item: ClaimedRun) -> None:
    _run_id = item.claim.run_id
    async with get_session_factory()() as db:
        try:
            async with run_trace(_run_id, item.session_id):
                with span("worker.execute"):
                    await RunService.create(
                        db, item.user_id, item.session_id, item.payload, item.key, _execute_existing=True
                    )
        except ExecutionNeedsRecovery:
            execution_health.fail(_run_id, "worker_requires_recovery")
            raise
        except Exception as exc:
            # Verify the assumption that RunService recorded an outcome. A DB
            # error in its error handler must not release an unaccounted owner.
            try:
                await db.rollback()
                saved = await db.get(AgentRunModel, _run_id, populate_existing=True)
                if saved is None or saved.status == AgentRunStatus.RUNNING:
                    raise ExecutionNeedsRecovery("Worker has no durable invocation outcome")
            except Exception as recovery_error:
                execution_health.fail(_run_id, "worker_outcome_unverified")
                raise ExecutionNeedsRecovery("Could not verify failed invocation outcome") from recovery_error
            logger.error("agent_run_failed run_id=%s error_type=%s", _run_id, type(exc).__name__)
            return


async def run_forever(*, stop_event: asyncio.Event | None = None) -> None:
    """Bounded per-process dispatcher; PostgreSQL arbitrates cross-process claims.

    Only one claim query is in flight, and only when a slot is available. An
    owned claim/handoff cannot be interrupted between DB commit and task tracking.
    """
    limit = settings.agent_worker_concurrency
    interval = max(0.05, settings.agent_worker_poll_interval_seconds)
    logger.info("agent_run_worker_started concurrency=%s poll_interval=%s", limit, interval)
    active: set[asyncio.Task] = set()
    claiming: asyncio.Task | None = None
    stopping = False
    stop_event = stop_event if stop_event is not None else asyncio.Event()
    stop_waiter = asyncio.create_task(stop_event.wait(), name="agent-run-stop")

    async def claim_and_start() -> bool:
        item = await claim_one()
        if item is None:
            return False
        if stopping or not execution_health.healthy:
            execution_health.fail(item.claim.run_id, "worker_stopped_after_claim")
            return False
        task = asyncio.create_task(execute_claimed(item), name=f"agent-run:{item.claim.run_id}")
        active.add(task)
        return True

    async def drain() -> None:
        # A commit in flight is allowed to settle; never silently lose its claim.
        if claiming is not None:
            await asyncio.gather(claiming, return_exceptions=True)
        for task in active:
            if not task.done():
                task.cancel()
        await asyncio.gather(*active, return_exceptions=True)
        active.clear()
        stop_waiter.cancel()
        await asyncio.gather(stop_waiter, return_exceptions=True)

    try:
        while not stop_event.is_set():
            for task in list(active):
                if task.done():
                    active.remove(task)
                    task.result()
            if not execution_health.healthy:
                raise ExecutionNeedsRecovery("Worker is unhealthy; no further claims allowed.")
            if len(active) < limit:
                claiming = asyncio.create_task(claim_and_start(), name="agent-run-claim")
                claimed = await asyncio.shield(claiming)
                claiming = None
                if claimed:
                    continue
                timeout = interval
            else:
                timeout = None
            if active:
                await asyncio.wait(active | {stop_waiter}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            else:
                await asyncio.wait({stop_waiter}, timeout=interval)
        # Cooperative stop: keep claims/heartbeats alive until calls return.
        # The service coordinator cancels us only after its drain deadline.
        if active:
            await asyncio.wait(active)
            for task in active:
                task.result()
    finally:
        stopping = True
        await protected_cleanup(drain())
