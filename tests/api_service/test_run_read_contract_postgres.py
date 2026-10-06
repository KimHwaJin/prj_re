"""Public Run summary/detail separation on guarded disposable PostgreSQL."""

from uuid import UUID

import pytest
from sqlalchemy import select, update

from dtest.contracts.enums import AgentRunStatus
from dtest.infrastructure.database.models.agent_run_model import (
    AgentRunModel as Run,
)
from tests.api_service.test_crud_guards_postgres import (
    database_url,
    harness,
    runtime,
    resources,
    seed,
)
from tests.api_service.test_read_queries_postgres import trace_reads
from tests.api_service.test_user_identity_postgres import headers

SUMMARY_FIELDS = {
    "run_id",
    "session_id",
    "status",
    "main_model_name",
    "model_revision",
    "recovery_required",
    "created_at",
    "updated_at",
    "started_at",
    "completed_at",
}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "pending",
        "running",
        "waiting_input",
        "waiting_executor",
        "success",
        "error",
        "timeout",
        "canceled",
        "recovery",
    ],
)
async def test_summary_matches_detail_without_large_payload_reads(
    resources, case
):
    h = resources
    await seed(h, case)
    base = f"/api/v1/sessions/{h.session_id}/runs"
    async with h.factory() as db:
        run = await db.scalar(
            select(Run).where(Run.session_id == UUID(h.session_id))
        )
        run.metadata_json = {
            "_model_selection": {"name": "test-model", "revision": "r1"}
        }
        run.agent_response = {"report": "private-result-" + "x" * 100000}
        run.failure = {"message": "private-failure-" + "y" * 100000}
        if case in (
            "pending",
            "running",
            "success",
            "error",
            "timeout",
            "canceled",
        ):
            run.status = AgentRunStatus(case)
        if case in ("waiting_input", "waiting_executor"):
            run.interrupt = [
                {**run.interrupt[0], "large_payload": "z" * 100000}
            ]
        await db.commit()
        rid = str(run.run_id)
    with trace_reads(h) as queries:
        response = await h.client.get(base, headers=headers(h.user["user_id"]))
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) == 1 and set(items[0]) == SUMMARY_FIELDS
    assert len(queries) == 4  # Auth, ownership, cursor page, batch summary.
    for query in queries:
        assert len(query["columns"]) <= 17
        assert (
            ".agent_response" not in query["sql"]
            and ".failure" not in query["sql"]
        )
        if ".interrupt" in query["sql"]:
            assert (
                "jsonb_path_exists" in query["sql"]
            )  # Boolean only, no interrupt body returned.
    detail = await h.client.get(
        base + "/" + rid, headers=headers(h.user["user_id"])
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert items[0] == {key: body[key] for key in SUMMARY_FIELDS}
    assert body["status"] == (
        "recovery_required" if case == "recovery" else case
    )
    if case in ("success", "error", "timeout", "canceled"):
        assert body["result"]["report"].startswith("private-result-")
    if case == "waiting_input":
        assert body["resume_token"] and body["interrupt"][0]["large_payload"]
    assert body["failure"]["message"].startswith("private-failure-")
    assert (
        "private-result" not in response.text
        and "private-failure" not in response.text
    )
    assert (
        await h.client.get(
            base + "/" + rid + "/join", headers=headers(h.user["user_id"])
        )
    ).status_code == 404
