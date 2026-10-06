"""Run collection projection independent of HTTP transport."""

from sqlalchemy import select
from sqlalchemy.orm import Bundle

from dtest.application.runs.repository import require_session
from dtest.application.runs.service import PublicRunService
from dtest.infrastructure.database.models.agent_run_model import AgentRunModel
from dtest.infrastructure.database.pagination import fetch_page


async def list_runs(db, user_id, session_id, params):
    await require_session(db, user_id, session_id)
    # Page only IDs/timestamps; hydrate the selected public states in one query.
    items, page = await fetch_page(
        db,
        select(
            Bundle("run_page", AgentRunModel.run_id, AgentRunModel.created_at)
        ).where(
            AgentRunModel.session_id == session_id,
            AgentRunModel.public_run_id == AgentRunModel.run_id,
        ),
        model=AgentRunModel,
        id_name="run_id",
        params=params,
    )
    summaries = await PublicRunService.summaries(
        db, [item.run_id for item in items]
    )
    return {"items": [summaries[item.run_id] for item in items], "page": page}
