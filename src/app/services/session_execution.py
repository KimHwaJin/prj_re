"""Cross-process graph ownership, without a DB connection held during inference.

An owner is not stolen on heartbeat expiry. Crash/uncertain termination requires
operator recovery after the old process is confirmed dead. This is not atomic
checkpoint fencing against an administrator forcibly replacing a live owner.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import logging
import os
import socket
from uuid import UUID, uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.core import database
from app.core.enums import TaskStatus
from app.models.common.task_model import TaskModel
from app.core.execution_lifecycle import (
    ExecutionNeedsRecovery, execution_health, observe_termination, protected_cleanup,
    wait_for_stop,
)
from app.models.common.session_execution_model import SessionExecutionModel as Owner
from app.services.helpers import utc_now
from app.worker.contracts import DeferEvent
from config import settings

logger = logging.getLogger(__name__)
PROCESS_ID = str(uuid4())


@dataclass(frozen=True)
class SessionExecution:
    session_id: UUID
    token: UUID
    owner_id: UUID
    kind: str


async def acquire(db, owner: SessionExecution) -> bool:
    """Caller commits; API queue claim and graph ownership share a transaction."""
    now = utc_now()
    values = dict(session_id=owner.session_id, token=owner.token,
                  owner_id=owner.owner_id, owner_kind=owner.kind,
                  owner_process=f"{socket.gethostname()}:{os.getpid()}:{PROCESS_ID}", acquired_at=now, heartbeat_at=now,
                  recovery_required=False, recovery_reason=None)
    result = await db.scalar(insert(Owner).values(**values).on_conflict_do_update(
        index_elements=[Owner.session_id],
        set_={key: value for key, value in values.items() if key != 'session_id'},
        where=Owner.token.is_(None) & Owner.recovery_required.is_(False),
    ).returning(Owner.session_id))
    return result is not None


async def _update(owner: SessionExecution, **values) -> None:
    async with asyncio.timeout(settings.run_monitor_timeout_seconds):
        async with database.get_session_factory()() as db:
            result = await db.execute(update(Owner).where(
                Owner.session_id == owner.session_id, Owner.token == owner.token,
                Owner.recovery_required.is_(False),
            ).values(**values).returning(Owner.session_id))
            if result.scalar_one_or_none() is None:
                raise ExecutionNeedsRecovery('Graph session ownership lost')
            await db.commit()


async def quarantine(owner: SessionExecution, reason: str) -> None:
    execution_health.fail(owner.owner_id, reason)
    try:
        await _update(owner, recovery_required=True, recovery_reason=reason)
    except Exception as exc:
        # Existing durable token still blocks takeover if this write cannot land.
        logger.error('session_recovery_record_failed session_id=%s error_type=%s',
                     owner.session_id, type(exc).__name__)


async def run_owned(owner: SessionExecution, operation: Callable[[], Awaitable]):
    """Validate before invocation; retain ownership until all child work stops."""
    stop = asyncio.Event()
    operation_task = None
    watcher = None

    async def monitor():
        while not stop.is_set():
            await wait_for_stop(stop, max(0.05, settings.task_lease_seconds / 3))
            if not stop.is_set():
                await _update(owner, heartbeat_at=utc_now())

    async def clean(uncertain: bool):
        stop.set()
        if uncertain:
            await quarantine(owner, 'session_execution_interrupted_or_uncertain')
        try:
            if operation_task is not None and not operation_task.done():
                await observe_termination(operation_task, run_id=owner.owner_id,
                                          stage='session_graph_stop', cancel=True)
        finally:
            if operation_task is not None:
                await asyncio.gather(operation_task, return_exceptions=True)
            if watcher is not None:
                await asyncio.gather(watcher, return_exceptions=True)

    uncertain = True
    try:
        if not execution_health.healthy:
            raise ExecutionNeedsRecovery('Process cannot start another graph')
        await _update(owner, heartbeat_at=utc_now())
        operation_task = asyncio.create_task(operation(), name=f'session-execution:{owner.session_id}')
        watcher = asyncio.create_task(monitor(), name=f'session-owner-monitor:{owner.session_id}')
        await asyncio.wait({operation_task, watcher}, return_when=asyncio.FIRST_COMPLETED)
        if watcher.done():
            await watcher
            raise ExecutionNeedsRecovery('Session monitor stopped unexpectedly')
        # No release for cancellation or recovery, even if the child has stopped.
        if operation_task.cancelled():
            raise asyncio.CancelledError
        error = operation_task.exception()
        if isinstance(error, ExecutionNeedsRecovery) or not execution_health.healthy:
            raise ExecutionNeedsRecovery('Session execution needs recovery') from error
        stop.set()
        await watcher
        # Child has returned, including graph writes and API state projection.
        await _update(owner, token=None, heartbeat_at=utc_now())
        uncertain = False
        return operation_task.result()
    except Exception as exc:
        if uncertain and not isinstance(exc, ExecutionNeedsRecovery):
            raise ExecutionNeedsRecovery('Session ownership verification failed') from exc
        raise
    finally:
        await protected_cleanup(clean(uncertain))


async def run_event_owned(context, operation: Callable[[], Awaitable]):
    if not execution_health.healthy:
        raise DeferEvent('Process session execution is unhealthy')
    owner = SessionExecution(UUID(context.session_id), uuid4(), context.command_id, 'executor_event')
    acquired = False

    async def acquire_owner():
        nonlocal acquired
        async with database.get_session_factory()() as db:
            busy_task = await db.scalar(select(TaskModel.task_id).where(
                TaskModel.session_id == owner.session_id,
                or_(TaskModel.status.in_([TaskStatus.PENDING, TaskStatus.RUNNING]),
                    TaskModel.recovery_required.is_(True)),
            ).limit(1))
            if busy_task is not None:
                raise DeferEvent('API task is executing, queued, or requires recovery')
            if not await acquire(db, owner):
                raise DeferEvent('Session graph is owned or requires recovery')
            await db.commit()
            acquired = True

    acquisition = asyncio.create_task(acquire_owner())
    try:
        await asyncio.shield(acquisition)
    except asyncio.CancelledError:
        async def finish_handoff():
            await asyncio.gather(acquisition, return_exceptions=True)
            if acquired:
                await quarantine(owner, 'event_cancelled_after_acquire')
        await protected_cleanup(finish_handoff())
        raise
    return await run_owned(owner, operation)
