"""Record outcomes only for the immutable owner that finished its graph call."""

from datetime import timedelta

from psycopg import Error as DatabaseError
from psycopg_pool import PoolTimeout
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from dtest.application.runs.commands.types import ClaimedEvent
from dtest.contracts.enums import AgentRunStatus
from dtest.contracts.events import DeferEvent, IgnoreEvent, RejectEvent
from dtest.contracts.execution import ExecutionNeedsRecovery
from dtest.contracts.values import utc_now
from dtest.infrastructure.database.models.agent_command_model import (
    AgentCommandModel as Command,
)
from dtest.infrastructure.database.models.agent_run_model import (
    AgentRunModel as Run,
)


def event_outcome(
    error, failed_attempts: int, max_failures: int
) -> tuple[str, int]:
    """Dependency deferral does not spend business retries or authorize takeover."""
    if error is not None and (
        not isinstance(error, Exception)
        or isinstance(error, ExecutionNeedsRecovery)
    ):
        return "RECOVERY", 0
    if isinstance(error, IgnoreEvent):
        return "IGNORED", 0
    if isinstance(
        error,
        (DeferEvent, SQLAlchemyError, DatabaseError, PoolTimeout, RedisError),
    ):
        return "READY", 0
    if error is None:
        return "DONE", 0
    terminal = (
        isinstance(error, RejectEvent) or failed_attempts + 1 >= max_failures
    )
    return ("FAILED" if terminal else "READY"), 1


async def record(
    factory, item, *, error=None, max_failures=5, retry_seconds=0.2
):
    async with factory() as db:
        row = await db.scalar(
            select(Command)
            .where(
                Command.namespace == item.namespace,
                Command.command_id == item.command_id,
            )
            .with_for_update()
        )
        if (
            row is None
            or row.state != "RUNNING"
            or row.owner_token != item.owner.token
        ):
            raise ExecutionNeedsRecovery("Command outcome owner changed")
        event = isinstance(item, ClaimedEvent)
        row.last_error = (
            f"{type(error).__name__}: {error}"[:2000] if error else None
        )
        if error is not None and not isinstance(error, Exception):
            row.state = "RECOVERY"
        elif isinstance(error, ExecutionNeedsRecovery):
            row.state = "RECOVERY"
        elif event:
            row.state, spent = event_outcome(
                error, row.failure_attempts, max_failures
            )
            row.failure_attempts += spent
            row.available_at = utc_now() + timedelta(seconds=retry_seconds)
        else:
            run = await db.get(Run, item.claim.run_id, populate_existing=True)
            if run is None or run.status == AgentRunStatus.RUNNING:
                row.state = "RECOVERY"
            elif run.status == AgentRunStatus.PENDING:
                row.state, row.available_at = (
                    "READY",
                    run.next_attempt_at or utc_now(),
                )
            else:
                row.state = "DONE"
        if row.state != "RECOVERY":
            row.owner_token = None
        row.updated_at = utc_now()
        await db.commit()
        return row.state
