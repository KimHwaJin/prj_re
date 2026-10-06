"""Cooperative shutdown with bounded observation and fail-closed ownership.

A Python task cannot be forcibly killed. If it ignores cancellation, keep its
owner/context alive, report an unhealthy process and prohibit another claim.
The deployment supervisor must terminate that process before manual recovery.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from dtest.contracts.execution import ExecutionNeedsRecovery
from dtest.settings.loader import get_settings

logger = logging.getLogger(__name__)


class ExecutionHealth:
    def __init__(self):
        self.faults: dict[str, str] = {}
        self.recorders: set[asyncio.Task] = set()

    @property
    def healthy(self):
        return not self.faults

    def fail(self, run_id: UUID, stage: str):
        key = str(run_id)
        if key in self.faults:
            return
        self.faults[key] = stage
        logger.error(
            "execution_requires_recovery run_id=%s stage=%s", key, stage
        )
        task = asyncio.create_task(
            self._record(run_id, stage), name=f"record-run-recovery:{key}"
        )
        self.recorders.add(task)
        task.add_done_callback(self.recorders.discard)

    async def _record(self, run_id, stage):
        from dtest.application.runs.tasks import TaskService
        from dtest.infrastructure.database.runtime import get_session_factory

        try:
            async with asyncio.timeout(
                get_settings().commands.run_cleanup_timeout_seconds
            ):
                async with get_session_factory()() as db:
                    await TaskService.require_recovery(
                        db, run_id=run_id, reason=stage
                    )
        except Exception as exc:
            logger.error(
                "recovery_record_failed run_id=%s error_type=%s",
                run_id,
                type(exc).__name__,
            )


execution_health = ExecutionHealth()


async def wait_for_stop(stop: asyncio.Event, interval: float):
    """Wake promptly on normal stop without cancelling a DB acquisition task."""
    try:
        async with asyncio.timeout(interval):
            await stop.wait()
    except TimeoutError:
        pass


async def observe_termination(
    task: asyncio.Task, *, run_id: UUID, stage: str, cancel=False
):
    """Return only after task termination, or quarantine while retaining ownership.

    On timely termination the caller handles the original result/exception.
    On deadline failure the exception is consumed before raising recovery.
    """
    if cancel and not task.done():
        task.cancel()
    _, pending = await asyncio.wait(
        {task}, timeout=get_settings().commands.run_cleanup_timeout_seconds
    )
    if not pending:
        return
    execution_health.fail(run_id, stage)
    task.cancel()
    _, pending = await asyncio.wait(
        {task}, timeout=get_settings().commands.run_cleanup_timeout_seconds
    )
    if not pending:
        # Graceful stop missed its deadline. Do not interpret canceled monitoring
        # or incomplete token flush as successful graph completion.
        await asyncio.gather(task, return_exceptions=True)
        raise ExecutionNeedsRecovery(stage)
    # Keep resources and the execution slot owned, even on repeated shutdown
    # cancellation. Readiness/liveness now report failure; no new work is claimed.
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
        except Exception:
            break
    await asyncio.gather(task, return_exceptions=True)
    raise ExecutionNeedsRecovery(stage)


async def finish_observer(task: asyncio.Task, *, run_id: UUID, stage: str):
    """A monitor/flush failure must not be retried as a business graph error."""
    try:
        await observe_termination(task, run_id=run_id, stage=stage)
        return task.result()
    except (Exception, asyncio.CancelledError) as exc:
        execution_health.fail(run_id, stage)
        raise ExecutionNeedsRecovery(stage) from exc
