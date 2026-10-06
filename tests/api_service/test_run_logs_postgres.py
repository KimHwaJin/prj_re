"""Diagnostic log contract on guarded disposable PostgreSQL."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from dtest.infrastructure.database.models import (
    AgentRunModel as Run,
    AgentRunLogModel as Log,
)
from tests.api_service.test_user_identity_postgres import (
    database_url,
    harness,
    initialize,
    add_user,
    add_session,
    headers,
)
from tests.api_service.test_read_queries_postgres import trace_reads


async def sample(h):
    await initialize(h)
    user = await add_user(h)
    sid = UUID(await add_session(h, user))
    other_sid = UUID(await add_session(h, user))
    root, alias, other = uuid4(), uuid4(), uuid4()
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ids = [uuid4() for _ in range(205)]
    async with h.factory() as db:
        for rid, public, session in (
            (root, root, sid),
            (alias, root, sid),
            (other, other, other_sid),
        ):
            db.add(
                Run(
                    run_id=rid,
                    public_run_id=public,
                    session_id=session,
                    idempotency_key=str(rid),
                    agent_response={"large_result": "x" * 100000},
                    failure={"large_failure": "y" * 100000},
                    interrupt=[{"large_interrupt": "z" * 100000}],
                )
            )
        await db.flush()
        for i, lid in enumerate(ids):
            db.add(
                Log(
                    log_id=lid,
                    run_id=root if i % 2 else alias,
                    event_key=f"key-{i}",
                    agent_name="planner" if i % 2 else "reviewer",
                    node="planning" if i % 2 else "review",
                    event="result" if i % 2 else "update",
                    kind="agent" if i % 2 else "public_event",
                    payload={"diagnostic": i},
                    created_at=stamp,
                )
            )
        db.add(
            Log(
                run_id=other,
                event_key="other",
                node="other",
                event="result",
                kind="agent",
                payload={},
            )
        )
        await db.commit()
    return user, sid, other_sid, root, alias, other, stamp, ids


@pytest.mark.asyncio
@pytest.mark.parametrize("sort", ["created_at", "-created_at"])
@pytest.mark.parametrize("limit", [1, 7, 200])
async def test_bounded_pages_across_invocations_without_heavy_run_reads(
    harness, sort, limit
):
    h = harness
    user, sid, _, root, alias, _, stamp, ids = await sample(h)
    cursor, seen = None, []
    while True:
        params = {"sort": sort, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        with trace_reads(h) as queries:
            response = await h.client.get(
                f"/api/v1/sessions/{sid}/runs/{alias}/logs",
                params=params,
                headers=headers(user["user_id"]),
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {"items", "page"}
        assert len(body["items"]) <= limit
        assert (
            len(queries) == 4
        )  # Auth, session ownership, canonical ID, log page.
        assert queries[-1]["rows"] <= limit + 1
        assert len(queries[-1]["columns"]) == 9
        assert len(queries[-2]["columns"]) == 1
        assert all(
            not any(
                private in q["sql"]
                for private in (
                    ".agent_response",
                    ".failure",
                    ".interrupt",
                    ".input",
                    ".command",
                    ".metadata",
                )
            )
            for q in queries
        )
        assert {item["run_id"] for item in body["items"]} <= {str(root)}
        seen.extend(UUID(item["log_id"]) for item in body["items"])
        if not body["page"]["has_next"]:
            assert body["page"]["next_cursor"] is None
            break
        cursor = body["page"]["next_cursor"]
    assert seen == sorted(ids, reverse=sort.startswith("-"))
    default = await h.client.get(
        f"/api/v1/sessions/{sid}/runs/{root}/logs",
        headers=headers(user["user_id"]),
    )
    assert (
        len(default.json()["items"]) == 50
        and default.json()["page"]["has_next"]
    )


@pytest.mark.asyncio
async def test_exact_filters_and_storage_time_bounds(harness):
    h = harness
    user, sid, _, root, _, _, stamp, _ = await sample(h)
    url = f"/api/v1/sessions/{sid}/runs/{root}/logs"
    for filters, count in [
        ({"agent_name": "planner"}, 102),
        ({"node": "review"}, 103),
        ({"event": "result"}, 102),
        ({"kind": "public_event"}, 103),
        (
            {
                "agent_name": "planner",
                "node": "planning",
                "event": "result",
                "kind": "agent",
            },
            102,
        ),
        ({"agent_name": "Planner"}, 0),
        ({"node": "missing"}, 0),
        (
            {
                "created_at_from": stamp.isoformat(),
                "created_at_to": (stamp + timedelta(seconds=1)).isoformat(),
            },
            200,
        ),
        ({"created_at_to": stamp.isoformat()}, 0),
    ]:
        response = await h.client.get(
            url,
            params={"limit": 200, **filters},
            headers=headers(user["user_id"]),
        )
        assert response.status_code == 200, response.text
        assert len(response.json()["items"]) == count
        if not count:
            assert response.json()["page"] == {
                "has_next": False,
                "next_cursor": None,
            }


@pytest.mark.asyncio
async def test_invalid_queries_and_owner_session_isolation(harness):
    h = harness
    user, sid, other_sid, root, alias, other, _, _ = await sample(h)
    url = f"/api/v1/sessions/{sid}/runs/{root}/logs"
    for params, code in [
        ({"limit": 0}, 422),
        ({"limit": 201}, 422),
        ({"sort": "node"}, 422),
        ({"cursor": "broken"}, 400),
        ({"agent_name": ""}, 422),
        ({"node": "x" * 101}, 422),
        ({"event": "x" * 101}, 422),
        ({"kind": "x" * 51}, 422),
        (
            {"created_at_from": "2026-02-01", "created_at_to": "2026-01-01"},
            422,
        ),
    ]:
        response = await h.client.get(
            url, params=params, headers=headers(user["user_id"])
        )
        assert response.status_code == code, response.text
    assert (await h.client.get(url)).status_code == 401
    assert (
        await h.client.get(url, headers=headers())
    ).status_code == 404  # Admin does not bypass ownership.
    for session, run in (
        (sid, other),
        (other_sid, root),
        (other_sid, alias),
        (sid, uuid4()),
    ):
        response = await h.client.get(
            f"/api/v1/sessions/{session}/runs/{run}/logs",
            headers=headers(user["user_id"]),
        )
        assert response.status_code == 404, response.text
    # Defensive against a malformed cross-session public mapping in legacy data.
    async with h.factory() as db:
        item = await db.get(Run, other)
        item.public_run_id = root
        await db.commit()
    assert (
        await h.client.get(
            f"/api/v1/sessions/{other_sid}/runs/{other}/logs",
            headers=headers(user["user_id"]),
        )
    ).status_code == 404
    body = (
        await h.client.get(
            url, params={"limit": 200}, headers=headers(user["user_id"])
        )
    ).json()
    assert all(item["event_key"] != "other" for item in body["items"])
