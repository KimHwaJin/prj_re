"""Retry deadlines and namespace-filtered signals; durable claim stays separate."""

from contextlib import asynccontextmanager
import hashlib

from sqlalchemy import func, select

from dtest.infrastructure.database.runtime import short_session
from dtest.infrastructure.database.models.agent_command_model import (
    AgentCommandModel as Command,
)
from dtest.contracts.values import utc_now
from dtest.infrastructure.database.signals import (
    COMMAND_CHANNEL,
    process_signals,
)


def namespace_signal(namespace: str) -> str:
    # Matches PostgreSQL md5(text); never used to authorize or identify a job.
    return hashlib.md5(
        namespace.encode("utf-8"), usedforsecurity=False
    ).hexdigest()


@asynccontextmanager
async def subscription(database_url, namespace, wake):
    signals = process_signals(database_url)
    selected = namespace_signal(namespace)

    def changed(payload):
        if payload is None or payload == selected:
            wake.set()

    async with signals.subscribe(COMMAND_CHANNEL, changed):
        yield signals


async def next_retry_delay(factory, namespace, *, observed_at=None):
    """A bounded indexed lookup only after an empty claim on a healthy listener.

    READY rows that were future at the claim start are hints, including blocked
    sessions. This also catches deadlines passing during the query. Older due
    rows are excluded to avoid spinning on an ineligible past deadline. Outcome
    notifications and the periodic scan handle ownership/order changes.
    """
    now = observed_at or utc_now()
    async with short_session(factory) as db:
        due = await db.scalar(
            select(func.min(Command.available_at)).where(
                Command.namespace == namespace,
                Command.state == "READY",
                Command.available_at > now,
            )
        )
    return (
        None if due is None else max(0.05, (due - utc_now()).total_seconds())
    )
