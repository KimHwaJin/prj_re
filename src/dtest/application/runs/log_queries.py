"""Owner-scoped, paginated diagnostic records for a stable public Run.

Read-side only: AgentRunLogService owns append-only log/event persistence.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Bundle, aliased

from dtest.contracts.pagination import ListParams
from dtest.infrastructure.database.pagination import fetch_page
from dtest.infrastructure.database.models import (
    AgentRunLogModel as Log,
    AgentRunModel as Run,
)
from dtest.application.runs.errors import RunNotFound
from dtest.application.runs.repository import require_session
from dtest.contracts.resources.run_schema import AgentRunLogResource


async def list_diagnostic_logs(
    db: AsyncSession,
    *,
    user_id: UUID,
    session_id: UUID,
    run_id: UUID,
    params: ListParams,
    agent_name: str | None = None,
    node: str | None = None,
    event: str | None = None,
    kind: str | None = None,
):
    await require_session(db, user_id, session_id)
    root = aliased(Run)
    # Legacy invocation IDs may resolve to the same public Run. Read only the
    # canonical ID, never the Run result/failure/interrupt payloads.
    public_id = await db.scalar(
        select(root.run_id)
        .join(
            Run,
            Run.public_run_id == root.run_id,
        )
        .where(
            Run.run_id == run_id,
            Run.session_id == session_id,
            root.session_id == session_id,
            root.public_run_id == root.run_id,
        )
    )
    if public_id is None:
        raise RunNotFound("Run not found.")
    stmt = (
        select(
            Bundle(
                "diagnostic_log",
                Log.log_id,
                Run.public_run_id.label("run_id"),
                Log.event_key,
                Log.agent_name,
                Log.node,
                Log.event,
                Log.kind,
                Log.payload,
                Log.created_at,
            )
        )
        .join(Run, Run.run_id == Log.run_id)
        .where(
            Run.public_run_id == public_id,
            Run.session_id == session_id,
        )
    )
    for name, value in (
        ("agent_name", agent_name),
        ("node", node),
        ("event", event),
        ("kind", kind),
    ):
        if value is not None:
            stmt = stmt.where(getattr(Log, name) == value)
    items, page = await fetch_page(
        db, stmt, model=Log, id_name="log_id", params=params
    )
    return {
        "items": [AgentRunLogResource.model_validate(item) for item in items],
        "page": page,
    }
