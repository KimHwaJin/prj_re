"""Synthetic event ownership for fault-injection tests, not a deployed Worker.

The real event admission/claim path is exercised by test_agent_commands_postgres
and test_executor_event_recovery_postgres. This harness acquires a test owner to
probe cancellation/resource guards without requiring a live Executor event.
"""
import asyncio
from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4
from sqlalchemy import or_, select
from fastapi import HTTPException
from api_service.infrastructure import database
from api_service.models.enums import TaskStatus
from api_service.models.task_model import TaskModel
from api_service.models.session_model import SessionModel
from api_service.models.session_execution_model import SessionExecutionModel as Owner
from api_service.resources import lifecycle
from api_service.runs.lifecycle import execution_health
from api_service.runs import ownership
from service_contracts.events import DeferEvent
from service_runtime.cleanup import protected_cleanup

class _HandoffPending(DeferEvent):
    """An API invocation is still committing/releasing this session."""


async def run_test_event(context, operation: Callable[[], Awaitable], *,
                          handoff_timeout_seconds: float = 0):
    if not execution_health.healthy:
        raise DeferEvent('Process session execution is unhealthy')
    owner = ownership.SessionExecution(UUID(context.session_id), uuid4(), context.command_id, 'executor_event')
    acquired = False

    async def acquire_owner():
        nonlocal acquired
        async with database.get_session_factory()() as db:
            api_session = await db.get(SessionModel, owner.session_id)
            if api_session is not None:
                try:
                    await lifecycle.lock_session(db, api_session.user_id, owner.session_id)
                except HTTPException as exc:
                    raise DeferEvent("API resources are inactive or changed during event admission") from exc
            # Standalone graph sessions without API resources still participate
            # in the existing persistent session ownership protocol.
            busy_task = await db.scalar(select(TaskModel.recovery_required).where(
                TaskModel.session_id == owner.session_id,
                or_(TaskModel.status.in_([TaskStatus.PENDING, TaskStatus.RUNNING]),
                    TaskModel.recovery_required.is_(True)),
            ).limit(1))
            if busy_task is True:
                raise DeferEvent('API task requires recovery')
            if busy_task is False:
                raise _HandoffPending('API task is executing or queued')
            if not await ownership.acquire(db, owner):
                current = await db.get(Owner, owner.session_id)
                if (current is not None and current.owner_kind == 'api_run'
                        and current.token is not None and not current.recovery_required):
                    raise _HandoffPending('API invocation has not released its session')
                raise DeferEvent('Session graph is owned or requires recovery')
            await db.commit()
            acquired = True

    async def bounded_handoff():
        deadline = asyncio.get_running_loop().time() + handoff_timeout_seconds
        while True:
            try:
                return await acquire_owner()
            except _HandoffPending:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise
                # acquire_owner exited its DB context. Never sleep with an open
                # transaction/connection, and never bypass or steal the owner.
                await asyncio.sleep(min(.05, remaining))

    acquisition = asyncio.create_task(bounded_handoff(), name=f'executor-event-admission:{owner.session_id}')
    try:
        await asyncio.shield(acquisition)
    except asyncio.CancelledError:
        async def finish_handoff():
            await asyncio.gather(acquisition, return_exceptions=True)
            if acquired:
                await ownership.quarantine(owner, 'event_cancelled_after_acquire')
        await protected_cleanup(finish_handoff())
        raise
    return await ownership.run_owned(owner, operation)
