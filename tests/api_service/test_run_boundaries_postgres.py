"""Verify execution UoW separation on an isolated real PostgreSQL database."""
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
import pytest

import api_service.runs.execution as execution
import api_service.workers.agent as worker
from api_service.models.enums import AgentRunStatus
from tests.api_service.test_user_identity_postgres import database_url, harness
from tests.api_service.test_run_cleanup_postgres import runtime, enqueue, rows


@pytest.mark.asyncio
async def test_preparation_session_closed_before_graph_and_result_uses_new_session(runtime, monkeypatch):
    h = runtime
    owned = []

    class ObservedSession(AsyncSession):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.was_closed = False
            owned.append(self)

        async def close(self):
            await super().close()
            self.was_closed = True

    factory = async_sessionmaker(h.engine, class_=ObservedSession, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(execution, "get_session_factory", lambda: factory)

    async def graph(**kwargs):
        assert len(owned) == 1
        assert owned[0].was_closed and not owned[0].in_transaction()
        return {"routing_result": {"route": "analysis"}}

    monkeypatch.setattr(execution, "ainvoke_user_turn", graph)
    public = await enqueue(h)
    await worker.execute_claimed(await worker.claim_one())
    run, task = await rows(h, public["run_id"])
    assert run.status == AgentRunStatus.SUCCESS
    assert len(owned) == 2 and all(session.was_closed for session in owned)
