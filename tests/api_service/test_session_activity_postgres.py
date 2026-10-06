"""Session capability, HTTP admission parity and bounded reads on disposable PG."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update

import dtest.worker_service.command_worker as worker
import dtest.application.runs.execution as runs
from dtest.contracts.enums import AgentRunStatus, TaskStatus
from dtest.infrastructure.database.models.agent_command_model import (
    AgentCommandModel as Command,
)
from dtest.infrastructure.database.models.agent_run_model import (
    AgentRunModel as Run,
)
from dtest.infrastructure.database.models.session_execution_model import (
    SessionExecutionModel as Owner,
)
from dtest.infrastructure.database.models.task_model import TaskModel as Task
from dtest.contracts.values import utc_now
from tests.api_service.test_crud_guards_postgres import (
    database_url,
    harness,
    runtime,
    resources,
    seed,
    new_session,
)
from tests.api_service.test_read_queries_postgres import trace_reads
from tests.api_service.test_run_cleanup_postgres import rows
from tests.api_service.test_user_identity_postgres import headers


AVAILABLE = {
    "status": "available",
    "allowed_actions": ["send_message"],
    "reason": None,
}
RESPOND = {
    "status": "available",
    "allowed_actions": ["respond_to_interaction"],
    "reason": None,
}


async def read(h):
    result = await h.client.get(
        f"/api/v1/sessions/{h.session_id}", headers=headers(h.user["user_id"])
    )
    assert result.status_code == 200, result.text
    return result.json()


async def start(h, key=None, session_id=None):
    return await h.client.post(
        f"/api/v1/sessions/{session_id or h.session_id}/runs",
        headers={
            **headers(h.user["user_id"]),
            "Idempotency-Key": key or str(uuid4()),
        },
        json={
            "input": {"content": [{"type": "text", "text": "test request"}]}
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case,status,action,reason",
    [
        ("pending", "busy", [], "processing"),
        ("running", "busy", [], "processing"),
        ("waiting_input", "available", ["respond_to_interaction"], None),
        ("waiting_executor", "busy", [], "waiting_external"),
        ("recovery", "blocked", [], "recovery_required"),
        ("owner_live", "busy", [], "processing"),
        ("owner_recovery", "blocked", [], "recovery_required"),
        ("orphan_pending", "busy", [], "processing"),
        ("orphan_running", "busy", [], "processing"),
        ("orphan_interrupted", "available", ["respond_to_interaction"], None),
    ],
)
async def test_capability_and_actual_new_request_agree(
    resources, case, status, action, reason
):
    h = resources
    await seed(h, case)
    body = await read(h)
    assert body["availability"] == dict(
        status=status, allowed_actions=action, reason=reason
    )
    if body["active_run"]:
        assert set(body["active_run"]) == {"run_id", "status"}
        run = await h.client.get(
            f"/api/v1/sessions/{h.session_id}/runs/{body['active_run']['run_id']}",
            headers=headers(h.user["user_id"]),
        )
        assert run.json()["status"] == body["active_run"]["status"]
    assert (await start(h)).status_code == 409
    sibling = (await new_session(h, h.project_id)).json()
    assert (
        sibling["availability"] == AVAILABLE and sibling["active_run"] is None
    )
    assert (await start(h, session_id=sibling["id"])).status_code == 202


@pytest.mark.asyncio
async def test_idle_create_read_rename_list_and_terminal_history(resources):
    h = resources
    await seed(h, "finished_history")
    await seed(h, "owner_released")
    body = await read(h)
    assert body["availability"] == AVAILABLE and body["active_run"] is None
    patch = await h.client.patch(
        f"/api/v1/sessions/{h.session_id}",
        headers=headers(h.user["user_id"]),
        json={"session_name": "changed"},
    )
    assert (
        patch.status_code == 200 and patch.json()["availability"] == AVAILABLE
    )
    listing = await h.client.get(
        f"/api/v1/projects/{h.project_id}/sessions",
        headers=headers(h.user["user_id"]),
    )
    assert listing.json()["items"] == [patch.json()]
    assert (await start(h)).status_code == 202


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["READY", "RUNNING", "RECOVERY"])
async def test_commands_lock_input_in_any_namespace(resources, state):
    h = resources
    await seed(h, "waiting_input")
    async with h.factory() as db:
        db.add(
            Command(
                namespace="other-namespace",
                command_id=uuid4(),
                session_id=UUID(h.session_id),
                kind="executor_resume",
                payload={"private": "never expose"},
                state=state,
            )
        )
        await db.commit()
    body = await read(h)
    assert body["availability"] == {
        "status": "blocked" if state == "RECOVERY" else "busy",
        "allowed_actions": [],
        "reason": "recovery_required" if state == "RECOVERY" else "processing",
    }
    assert "private" not in str(body)
    assert (await start(h)).status_code == 409


@pytest.mark.asyncio
async def test_cancel_requested_and_terminal_owner_release_interval(resources):
    h = resources
    await seed(h, "waiting_input")
    async with h.factory() as db:
        await db.execute(
            update(Task)
            .where(Task.session_id == UUID(h.session_id))
            .values(cancel_requested_at=utc_now())
        )
        await db.commit()
    assert (await read(h))["availability"] == {
        "status": "busy",
        "allowed_actions": [],
        "reason": "canceling",
    }
    assert (await start(h)).status_code == 409
    async with h.factory() as db:
        await db.execute(
            update(Task)
            .where(Task.session_id == UUID(h.session_id))
            .values(status=TaskStatus.CANCELED)
        )
        db.add(
            Owner(
                session_id=UUID(h.session_id),
                token=uuid4(),
                heartbeat_at=utc_now() - timedelta(days=1),
            )
        )
        await db.commit()
    body = await read(h)
    assert (
        body["active_run"] is None and body["availability"]["status"] == "busy"
    )
    assert (
        await start(h)
    ).status_code == 409  # Expired heartbeat never unlocks ownership.
    async with h.factory() as db:
        await db.execute(
            update(Owner)
            .where(Owner.session_id == UUID(h.session_id))
            .values(token=None)
        )
        await db.commit()
    assert (await read(h))["availability"] == AVAILABLE
    assert (await start(h)).status_code == 202


@pytest.mark.asyncio
async def test_latest_invocation_replaces_historical_taskless_interrupt(
    resources,
):
    h = resources
    await seed(h, "orphan_interrupted")
    async with h.factory() as db:
        root = await db.scalar(
            select(Run).where(Run.session_id == UUID(h.session_id))
        )
        db.add(
            Run(
                session_id=root.session_id,
                public_run_id=root.run_id,
                idempotency_key="completed",
                status=AgentRunStatus.SUCCESS,
                created_at=root.created_at + timedelta(seconds=1),
            )
        )
        await db.commit()
    assert (await read(h))["availability"] == AVAILABLE
    assert (await start(h)).status_code == 202


@pytest.mark.asyncio
async def test_multiple_active_public_runs_block_without_arbitrary_resume(
    resources,
):
    h = resources
    await seed(h, "orphan_interrupted")
    await seed(h, "orphan_pending")
    body = await read(h)
    assert body["availability"] == {
        "status": "blocked",
        "allowed_actions": [],
        "reason": "recovery_required",
    }
    assert (await start(h)).status_code == 409


@pytest.mark.asyncio
async def test_hitl_resume_stable_identity_and_replays_during_recovery(
    resources, monkeypatch
):
    h = resources
    monkeypatch.setattr(
        runs,
        "ainvoke_user_turn",
        AsyncMock(
            return_value={
                "__interrupt__": [
                    SimpleNamespace(
                        value={"question": "approve?"}, id="test-hitl"
                    )
                ]
            }
        ),
    )
    monkeypatch.setattr(
        runs, "ainvoke_resume", AsyncMock(return_value={"answer": "done"})
    )
    key = str(uuid4())
    initial = await start(h, key)
    assert initial.status_code == 202, initial.text
    public_id = initial.json()["run_id"]
    assert (await read(h))["availability"]["reason"] == "processing"
    await worker.execute_claimed(await worker.claim_one())
    invocation, task = await rows(h, public_id)
    summary = await read(h)
    assert summary["active_run"] == {
        "run_id": public_id,
        "status": "waiting_input",
    }
    assert summary["availability"] == RESPOND
    assert (await start(h)).status_code == 409
    resume_key = str(uuid4())
    payload = {
        "run_id": public_id,
        "resume_token": str(invocation.run_id),
        "command": {
            "resume": {
                "action": "approve_plan",
                "plan_id": "test-plan",
                "plan_revision": 1,
            }
        },
    }

    async def resume():
        return await h.client.post(
            f"/api/v1/sessions/{h.session_id}/runs",
            headers={
                **headers(h.user["user_id"]),
                "Idempotency-Key": resume_key,
            },
            json=payload,
        )

    accepted = await resume()
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["run_id"] == public_id
    async with h.factory() as db:
        await db.execute(
            update(Owner)
            .where(Owner.session_id == UUID(h.session_id))
            .values(recovery_required=True)
        )
        await db.commit()
    assert (await read(h))["availability"]["status"] == "blocked"
    assert (
        await start(h, key)
    ).status_code == 202  # Replay is not a new action.
    assert (await resume()).status_code == 202
    assert (await start(h)).status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [1, 7, 200])
@pytest.mark.parametrize("sort", ["created_at", "-created_at"])
async def test_paginated_session_snapshots_have_constant_read_budget(
    resources, limit, sort
):
    h = resources
    ids = {h.session_id}
    for _ in range(19):
        ids.add((await new_session(h, h.project_id)).json()["id"])
    await seed(h, "waiting_executor")
    seen, cursor = [], None
    while True:
        params = {"limit": limit, "sort": sort}
        if cursor:
            params["cursor"] = cursor
        with trace_reads(h) as reads:
            result = await h.client.get(
                f"/api/v1/projects/{h.project_id}/sessions",
                params=params,
                headers=headers(h.user["user_id"]),
            )
        assert result.status_code == 200, result.text
        body = result.json()
        assert (
            len(reads) == 3
        )  # Auth + project ownership + one paginated snapshot.
        assert len(body["items"]) <= limit
        for query in reads:
            assert all(
                field not in query["sql"]
                for field in (
                    ".agent_response",
                    ".metadata",
                    ".request_payload",
                    ".input",
                    ".failure",
                    "FROM messages",
                )
            )
        for item in body["items"]:
            if item["id"] == h.session_id:
                assert item["availability"]["reason"] == "waiting_external"
            else:
                assert (
                    item["availability"] == AVAILABLE
                    and item["active_run"] is None
                )
            seen.append(item["id"])
        if not body["page"]["has_next"]:
            break
        cursor = body["page"]["next_cursor"]
    assert len(seen) == len(set(seen)) == 20 and set(seen) == ids


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["DONE", "IGNORED", "FAILED"])
async def test_terminal_command_history_does_not_block_hitl(resources, state):
    h = resources
    await seed(h, "waiting_input")
    async with h.factory() as db:
        db.add(
            Command(
                namespace="other-namespace",
                command_id=uuid4(),
                session_id=UUID(h.session_id),
                kind="executor_resume",
                payload={"private": "never expose"},
                state=state,
            )
        )
        await db.commit()
    assert (await read(h))["availability"] == RESPOND


@pytest.mark.asyncio
async def test_task_without_public_run_never_implies_idle(resources):
    h = resources
    async with h.factory() as db:
        db.add(
            Task(
                session_id=UUID(h.session_id),
                status=TaskStatus.PENDING,
                idempotency_key="no-run-yet",
            )
        )
        await db.commit()
    body = await read(h)
    assert body["active_run"] is None
    assert body["availability"] == {
        "status": "busy",
        "allowed_actions": [],
        "reason": "processing",
    }
    assert (await start(h)).status_code == 409


@pytest.mark.asyncio
async def test_historical_taskless_interrupt_no_longer_blocks_deletion(
    resources,
):
    h = resources
    await seed(h, "orphan_interrupted")
    async with h.factory() as db:
        root = await db.scalar(
            select(Run).where(Run.session_id == UUID(h.session_id))
        )
        db.add(
            Run(
                session_id=root.session_id,
                public_run_id=root.run_id,
                idempotency_key="completed",
                status=AgentRunStatus.SUCCESS,
                created_at=root.created_at + timedelta(seconds=1),
            )
        )
        await db.commit()
    result = await h.client.delete(
        f"/api/v1/sessions/{h.session_id}", headers=headers(h.user["user_id"])
    )
    assert result.status_code == 204, result.text
    assert (
        await h.client.get(
            f"/api/v1/sessions/{h.session_id}",
            headers=headers(h.user["user_id"]),
        )
    ).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "rename"])
async def test_committed_mutation_then_delete_preserves_success_and_blocks_input(
    resources, monkeypatch, operation
):
    import asyncio
    from dtest.application.resources.sessions import SessionService

    h = resources
    entered, release = asyncio.Event(), asyncio.Event()
    method = "create" if operation == "create" else "update"
    original = getattr(SessionService, method)

    async def hold_after_commit(*args, **kwargs):
        result = await original(*args, **kwargs)
        entered.set()
        await release.wait()
        return result

    monkeypatch.setattr(SessionService, method, hold_after_commit)
    if operation == "create":
        first = asyncio.create_task(new_session(h, h.project_id))
        path = f"/api/v1/projects/{h.project_id}"
    else:
        first = asyncio.create_task(
            h.client.patch(
                f"/api/v1/sessions/{h.session_id}",
                headers=headers(h.user["user_id"]),
                json={"session_name": "committed"},
            )
        )
        path = f"/api/v1/sessions/{h.session_id}"
    try:
        await asyncio.wait_for(entered.wait(), 5)
        deleted = await h.client.delete(
            path, headers=headers(h.user["user_id"])
        )
        assert deleted.status_code == 204, deleted.text
    finally:
        release.set()
    result = await asyncio.wait_for(first, 5)
    assert result.status_code == (201 if operation == "create" else 200), (
        result.text
    )
    body = result.json()
    assert body["availability"] == {
        "status": "blocked",
        "allowed_actions": [],
        "reason": "resource_unavailable",
    }
    later = await h.client.get(
        f"/api/v1/sessions/{body['id']}", headers=headers(h.user["user_id"])
    )
    assert later.status_code == 404
