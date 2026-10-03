"""One bounded Agent Worker for user input and durable Executor result commands."""

from __future__ import annotations

import asyncio
import logging

from config import settings
from api_service.core.database import get_session_factory
from service_contracts.execution import ExecutionNeedsRecovery
from api_service.core.execution_lifecycle import execution_health
from service_runtime.cleanup import protected_cleanup
from api_service.core.execution_claim import bind_execution_claim
from api_service.core.enums import AgentRunStatus
from api_service.models.common.agent_run_model import AgentRunModel
from api_service.services.session_execution import run_owned
from api_service.runs.commands.claim import claim_one as claim_command
from api_service.runs.commands.outcome import record as record_outcome
from api_service.runs.commands.types import ClaimedCommand, ClaimedRun
from api_service.runs.execution import execute_claimed as execute_invocation
from service_runtime.diagnostics import run_trace, span

logger = logging.getLogger(__name__)


async def claim_one() -> ClaimedCommand | None:
    if not execution_health.healthy:
        raise ExecutionNeedsRecovery("Worker is unhealthy; no further claims allowed.")
    from service_settings import get_settings
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
        from service_settings import get_settings
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
    from api_service.services.agent_graph_service import runtime
    from api_service.services.agent_project_context import load_event_project_snapshot
    from api_service.runs.graph_invocation import GraphInvocation
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
