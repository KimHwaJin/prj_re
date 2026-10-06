"""Behavioral checks for shared execution, submission tracking and projection."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from dtest.application.runs.graph_invocation import GraphInvocation
from dtest.application.runs import monitoring
from dtest.application.runs.errors import CancellationRequested
from dtest.infrastructure.executor.client import (
    ExecutorOutcomeUnknown,
    SubmissionEffects,
    current_submission_effects,
    submission_scope,
)


@pytest.mark.asyncio
async def test_nested_graph_records_submission_in_owners_tracker():
    effects = SubmissionEffects()

    async def invoke(*args, **kwargs):
        assert current_submission_effects() is effects
        effects.possible_submissions.add("accepted-post")
        return {"done": True}

    with submission_scope(effects):
        result = await GraphInvocation(SimpleNamespace(ainvoke=invoke)).invoke(
            {}, {}
        )
        assert result == {"done": True}
        assert effects.may_have_submitted
    assert current_submission_effects() is None


@pytest.mark.asyncio
async def test_concurrent_invocations_isolate_submission_trackers():
    arrived = 0
    ready = asyncio.Event()

    async def one():
        async def invoke(*args, **kwargs):
            nonlocal arrived
            tracker = current_submission_effects()
            tracker.possible_submissions.add(id(tracker))
            arrived += 1
            if arrived == 2:
                ready.set()
            await ready.wait()
            assert current_submission_effects() is tracker
            assert tracker.possible_submissions == {id(tracker)}
            return tracker

        return await GraphInvocation(SimpleNamespace(ainvoke=invoke)).invoke(
            {}, {}
        )

    first, second = await asyncio.wait_for(asyncio.gather(one(), one()), 1)
    assert first is not second
    assert current_submission_effects() is None


@pytest.mark.asyncio
async def test_cancel_after_nested_submission_is_not_safe_local_cancel(
    monkeypatch,
):
    submitted = asyncio.Event()

    async def graph(*args, **kwargs):
        current_submission_effects().possible_submissions.add(
            "post-may-be-accepted"
        )
        submitted.set()
        await asyncio.Event().wait()

    async def cancel(run_id, stop):
        await submitted.wait()
        return True

    monkeypatch.setattr(monitoring, "wait_for_cancellation", cancel)
    boundary = GraphInvocation(SimpleNamespace(ainvoke=graph))
    with pytest.raises(ExecutorOutcomeUnknown):
        await monitoring.run_cancellable(uuid4(), boundary.invoke({}, {}))
    assert current_submission_effects() is None
    assert not [
        t
        for t in asyncio.all_tasks()
        if t is not asyncio.current_task()
        and t.get_name().startswith("graph:")
    ]


@pytest.mark.asyncio
async def test_stream_skips_old_input_echo_and_preserves_sync_durability(
    monkeypatch,
):
    from dtest.application.runs.persistence import graph as projection

    saved = AsyncMock()
    monkeypatch.setattr(projection.InvocationProjection, "persist", saved)
    uid, rid = uuid4(), uuid4()
    emitted = [
        {"initial_request_receipt": "old", "public_events": ["old"]},
        {
            "initial_request_receipt": "new",
            "user_id": str(uid),
            "agent_run_id": str(rid),
        },
    ]
    calls = []

    async def stream(value, **kwargs):
        calls.append(kwargs)
        for state in emitted:
            yield state

    boundary = GraphInvocation(
        SimpleNamespace(name="agentic-planning-v1", astream=stream)
    )
    await boundary.invoke(
        {},
        {},
        user_id=uid,
        agent_run_id=rid,
        accept_state=lambda s: s["initial_request_receipt"] == "new",
    )
    assert saved.await_count == 1 and saved.call_args.args[0] is emitted[1]
    assert calls == [
        {
            "config": {"recursion_limit": 100},
            "stream_mode": "values",
            "durability": "sync",
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_name,status",
    [
        ("RunNotFound", 404),
        ("RunConflict", 409),
        ("InvalidRunRequest", 422),
        ("RunUnavailable", 503),
        ("RunExecutionFailed", 502),
    ],
)
async def test_application_errors_keep_public_problem_contract(
    error_name, status
):
    import httpx
    from fastapi import FastAPI
    from dtest.api_service.http.problems import run_exception_handler
    from dtest.application.runs import errors
    from dtest.api_service.middleware.request_id import RequestIdMiddleware

    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(errors.RunError, run_exception_handler)

    @app.get("/test")
    async def fail():
        raise getattr(errors, error_name)("same public detail")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/test")
    assert response.status_code == status
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["detail"] == "same public detail"
    assert response.json()["request_id"]


@pytest.mark.asyncio
async def test_execution_without_claim_cannot_open_database(monkeypatch):
    from dtest.application.runs import execution
    from dtest.contracts.execution import ExecutionNeedsRecovery

    opened = []
    monkeypatch.setattr(
        execution, "get_session_factory", lambda: opened.append(True)
    )
    with pytest.raises(ExecutionNeedsRecovery):
        await execution.prepare(None, uuid4(), uuid4(), None)
    assert not opened


def test_run_application_modules_have_no_http_dependency():
    import ast
    from pathlib import Path

    package = (
        Path(__file__).resolve().parents[2] / "src/dtest/application/runs"
    )
    # Transport/resource adapters were previously under services; application execution stays HTTP-free.
    adapters = {
        "service.py",
        "streaming.py",
        "diagnostics.py",
        "ownership.py",
        "runtime.py",
    }
    for source in package.rglob("*.py"):
        if source.parent == package and source.name in adapters:
            continue
        for node in ast.walk(ast.parse(source.read_text())):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(
                    ("fastapi", "starlette")
                ), source
            elif isinstance(node, ast.Import):
                assert not any(
                    alias.name.startswith(("fastapi", "starlette"))
                    for alias in node.names
                ), source
