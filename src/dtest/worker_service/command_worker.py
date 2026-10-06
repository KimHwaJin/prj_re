"""One bounded Agent Worker for user input and durable Executor result commands."""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack

from dtest.settings.loader import get_settings
from dtest.infrastructure.database.runtime import get_session_factory
from dtest.contracts.execution import ExecutionNeedsRecovery
from dtest.application.runs.lifecycle import execution_health
from dtest.lifecycle import protected_cleanup
from dtest.application.runs.claim_context import bind_execution_claim
from dtest.contracts.enums import AgentRunStatus
from dtest.infrastructure.database.models.agent_run_model import AgentRunModel
from dtest.application.runs.ownership import run_owned
from dtest.application.runs.commands.claim import claim_one as claim_command
from dtest.application.runs.commands.outcome import record as record_outcome
from dtest.application.runs.commands.types import ClaimedCommand, ClaimedRun
from dtest.application.runs.commands.wakeup import subscription, next_retry_delay
from dtest.application.runs.execution import execute_claimed as execute_invocation
from dtest.infrastructure.observability.diagnostics import run_trace, span
from dtest.contracts.values import utc_now

logger = logging.getLogger(__name__)


async def claim_one() -> ClaimedCommand | None:
    if not execution_health.healthy:
        raise ExecutionNeedsRecovery("Worker is unhealthy; no further claims allowed.")
    return await claim_command(get_session_factory(), get_settings().worker.namespace)


async def execute_claimed(item: ClaimedCommand) -> None:
    """Both input kinds use the same owned graph call and durable completion."""
    error = None
    try:
        if isinstance(item, ClaimedRun):
            with bind_execution_claim(item.claim):
                await run_owned(item.owner, lambda: _execute_claimed(item))
        else:
            async with run_trace(item.command_id, item.session_id):
                await run_owned(item.owner, lambda: execute_event(item.context))
    except BaseException as exc:
        error = exc
    try:
        await record_outcome(get_session_factory(), item, error=error,
                             max_failures=get_settings().worker.max_handler_attempts)
    except BaseException as recording_error:
        execution_health.fail(item.command_id, "command_outcome_unverified")
        raise ExecutionNeedsRecovery("Could not verify durable command outcome") from recording_error
    if error is not None:
        if not isinstance(error, Exception) or isinstance(error, ExecutionNeedsRecovery):
            execution_health.fail(item.command_id, "command_execution_uncertain")
            raise error
        logger.warning("command_deferred_or_failed command_id=%s error_type=%s", item.command_id, type(error).__name__)


async def execute_event(context) -> None:
    from dtest.application.runs.runtime import runtime
    from dtest.application.runs.project_context import load_event_project_snapshot
    from dtest.application.runs.graph_invocation import GraphInvocation
    async with runtime.open_graph() as graph:
        await GraphInvocation(graph, project_context_loader=load_event_project_snapshot).executor_event(context)


async def _execute_claimed(item: ClaimedRun) -> None:
    _run_id = item.claim.run_id
    try:
        async with run_trace(_run_id, item.session_id):
            with span("worker.execute"):
                await execute_invocation(item.claim, item.user_id, item.session_id, item.payload)
    except ExecutionNeedsRecovery:
        execution_health.fail(_run_id, "worker_requires_recovery")
        raise
    except Exception as exc:
        # An error may be terminal only after its durable outcome is verified.
        try:
            async with get_session_factory()() as db:
                saved = await db.get(AgentRunModel, _run_id, populate_existing=True)
                if saved is None or saved.status == AgentRunStatus.RUNNING:
                    raise ExecutionNeedsRecovery("Worker has no durable invocation outcome")
        except Exception as recovery_error:
            execution_health.fail(_run_id, "worker_outcome_unverified")
            raise ExecutionNeedsRecovery("Could not verify failed invocation outcome") from recovery_error
        logger.error("agent_run_failed run_id=%s error_type=%s", _run_id, type(exc).__name__)


async def run_forever(*, stop_event: asyncio.Event | None = None) -> None:
    """Bounded per-process dispatcher; PostgreSQL arbitrates cross-process claims.

    Only one claim query is in flight, and only when a slot is available. An
    owned claim/handoff cannot be interrupted between DB commit and task tracking.
    """
    limit = get_settings().commands.agent_worker_concurrency
    interval = max(0.05, get_settings().commands.agent_worker_poll_interval_seconds)
    logger.info("agent_run_worker_started concurrency=%s notify_enabled=%s fallback_poll_seconds=%s reconcile_seconds=%s",
                limit, get_settings().commands.agent_worker_notify_enabled, interval, get_settings().commands.agent_worker_reconcile_interval_seconds)
    wake = asyncio.Event()
    stack = AsyncExitStack()
    signals = None
    wake_waiter = None
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
            execution_health.fail(item.command_id, "worker_stopped_after_claim")
            return False
        task = asyncio.create_task(execute_claimed(item), name=f"agent-run:{item.command_id}")
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
        if wake_waiter is not None:
            wake_waiter.cancel()
            await asyncio.gather(wake_waiter, return_exceptions=True)
        await asyncio.gather(stop_waiter, return_exceptions=True)
        await stack.aclose()

    try:
        configured = get_settings()
        if get_settings().commands.agent_worker_notify_enabled:
            signals = await stack.enter_async_context(subscription(
                get_settings().database.database_url, configured.worker.namespace, wake))
        while not stop_event.is_set():
            for task in list(active):
                if task.done():
                    active.remove(task)
                    task.result()
            if not execution_health.healthy:
                raise ExecutionNeedsRecovery("Worker is unhealthy; no further claims allowed.")
            if len(active) < limit:
                # Clear BEFORE the DB claim. A commit racing with the query
                # sets it again and cannot be lost between query and sleep.
                wake.clear()
                observed_at = utc_now()
                claiming = asyncio.create_task(claim_and_start(), name="agent-run-claim")
                claimed = await asyncio.shield(claiming)
                claiming = None
                if claimed:
                    continue
                timeout = interval
                if signals is not None and signals.ready.is_set():
                    timeout = max(.05, get_settings().commands.agent_worker_reconcile_interval_seconds)
                    due = await next_retry_delay(get_session_factory(), configured.worker.namespace, observed_at=observed_at)
                    if due is not None:
                        timeout = min(timeout, due)
                wake_waiter = asyncio.create_task(wake.wait(), name='agent-command-wake')
                waiters = active | {stop_waiter, wake_waiter}
            else:
                # Ignore a set wake while full; keep it coalesced until a slot
                # returns. Waiting on it here would spin without any capacity.
                timeout = None
                waiters = active | {stop_waiter}
            try:
                await asyncio.wait(waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            finally:
                if wake_waiter is not None:
                    wake_waiter.cancel()
                    await asyncio.gather(wake_waiter, return_exceptions=True)
                    wake_waiter = None
        # Cooperative stop: keep claims/heartbeats alive until calls return.
        # The service coordinator cancels us only after its drain deadline.
        if active:
            await asyncio.wait(active)
            for task in active:
                task.result()
    finally:
        stopping = True
        await protected_cleanup(drain())
