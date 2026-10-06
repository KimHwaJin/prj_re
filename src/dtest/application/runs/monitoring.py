"""Own graph cancellation and observer termination before releasing execution."""

import asyncio
from typing import Any, Awaitable
from uuid import UUID

from sqlalchemy import select

from dtest.infrastructure.database.runtime import get_session_factory
from dtest.application.runs.lifecycle import (
    execution_health,
    finish_observer,
    observe_termination,
    wait_for_stop,
)
from dtest.infrastructure.database.models.agent_run_model import AgentRunModel
from dtest.application.runs.errors import CancellationRequested
from dtest.settings.loader import get_settings
from dtest.settings.api import settings
from dtest.contracts.execution import ExecutionNeedsRecovery
from dtest.lifecycle import protected_cleanup


async def wait_for_cancellation(run_id: UUID, stop: asyncio.Event) -> bool:
    """별도 DB session으로 취소 요청을 감시해 다른 API worker의 요청도 감지합니다."""
    interval = max(0.05, get_settings().commands.task_cancel_poll_interval_seconds)
    while not stop.is_set():
        async with asyncio.timeout(get_settings().commands.run_monitor_timeout_seconds):
            async with get_session_factory()() as cancellation_db:
                requested_at = await cancellation_db.scalar(
                    select(AgentRunModel.cancel_requested_at).where(
                        AgentRunModel.run_id == run_id
                    )
                )
        if requested_at is not None:
            return True
        await wait_for_stop(stop, interval)
    return False


async def run_cancellable(
    run_id: UUID,
    graph_awaitable: Awaitable[dict[str, Any]],
    *,
    observers: tuple[asyncio.Task, ...] = (),
) -> dict[str, Any]:
    """Graph와 DB cancel watcher를 경쟁시켜 실제 coroutine을 cooperative cancel합니다."""
    stop = asyncio.Event()
    from dtest.infrastructure.executor.client import submission_scope, SubmissionEffects, ExecutorOutcomeUnknown
    effects = SubmissionEffects()
    async def invoke():
        with submission_scope(effects):
            return await graph_awaitable
    graph_task = asyncio.create_task(invoke(), name=f"graph:{run_id}")
    cancel_task = asyncio.create_task(wait_for_cancellation(run_id, stop), name=f"cancel-watch:{run_id}")

    async def cleanup():
        stop.set()
        errors = []
        try:
            await observe_termination(graph_task, run_id=run_id, stage="graph_stop", cancel=True)
        except ExecutionNeedsRecovery as exc:
            errors.append(exc)
        finally:
            # Retrieve the exception even when cancellation/another observer
            # won the race; the selected graph result is handled below.
            outcomes = await asyncio.gather(graph_task, return_exceptions=True)
            import inspect
            if inspect.iscoroutine(graph_awaitable) and inspect.getcoroutinestate(graph_awaitable) == inspect.CORO_CREATED:
                graph_awaitable.close()
            if isinstance(outcomes[0], ExecutionNeedsRecovery):
                errors.append(outcomes[0])
        try:
            await finish_observer(cancel_task, run_id=run_id, stage="cancel_watch_stop")
        except ExecutionNeedsRecovery as exc:
            errors.append(exc)
        if errors:
            raise errors[0]

    try:
        done, _ = await asyncio.wait(
            {graph_task, cancel_task, *observers},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for observer in observers:
            if observer in done:
                execution_health.fail(run_id, f"observer_stopped:{observer.get_name()}")
                # Heartbeat/token consumer may only finish after graph stop.
                raise ExecutionNeedsRecovery("Execution observer stopped before the graph.")
        if cancel_task in done:
            requested = await finish_observer(cancel_task, run_id=run_id, stage="cancel_watch_failed")
            if requested:
                if effects.may_have_submitted:
                    raise ExecutorOutcomeUnknown("Cancellation raced with Executor submission; reconcile before releasing the session")
                raise CancellationRequested
        return await graph_task
    finally:
        await protected_cleanup(cleanup())
