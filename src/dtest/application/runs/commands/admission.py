"""Caller owns the transaction; ledger admission never commits independently."""

from sqlalchemy import func, select

from dtest.infrastructure.database.models.agent_command_model import (
    AgentCommandModel,
)
from dtest.settings.loader import get_settings


async def enqueue_user(db, run) -> None:
    # The same advisory key is used by Inbox routing. It serializes insert and
    # commit per session so an invisible earlier transaction cannot be overtaken.
    await db.execute(
        select(
            func.pg_advisory_xact_lock(
                func.hashtextextended(
                    f"agent-command-order:{run.session_id}", 0
                )
            )
        )
    )
    db.add(
        AgentCommandModel(
            namespace=get_settings().worker.namespace,
            command_id=run.run_id,
            session_id=run.session_id,
            invocation_id=run.run_id,
            kind="user_resume"
            if run.command_json is not None
            else "user_start",
        )
    )
    await db.flush()
