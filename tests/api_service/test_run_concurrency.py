"""Bounded dispatch, claim handoff and owned shutdown; no external services."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

import dtest.settings.loader as service_settings
import dtest.worker_service.command_worker as worker
from dtest.application.runs.lifecycle import execution_health
from dtest.contracts.execution import ExecutionNeedsRecovery


@pytest_asyncio.fixture(autouse=True)
async def isolated(monkeypatch):
    monkeypatch.setattr(service_settings, "_snapshot", None)
    service_settings.configure(
        service_settings.load_settings(
            config={
                "AGENT_WORKER_NOTIFY_ENABLED": False,
                "AGENT_WORKER_CONCURRENCY": 3,
                "AGENT_WORKER_POLL_INTERVAL_SECONDS": 0.05,
            },
            environ={},
        )
    )
    monkeypatch.setattr(execution_health, "faults", {})
    monkeypatch.setattr(execution_health, "recorders", set())
    monkeypatch.setattr(execution_health, "_record", AsyncMock())
    yield
    await asyncio.gather(*execution_health.recorders)


def item():
    identity = uuid4()
    return SimpleNamespace(
        command_id=identity, claim=SimpleNamespace(run_id=identity)
    )


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.001)


async def stop(task):
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_capacity_no_prefetch_and_immediate_slot_reuse(monkeypatch):
    claims, started, gates = [], [], []

    async def claim():
        value = item()
        claims.append(value)
        return value

    async def execute(value):
        started.append(value)
        gate = asyncio.Event()
        gates.append(gate)
        await gate.wait()

    monkeypatch.setattr(worker, "claim_one", claim)
    monkeypatch.setattr(worker, "execute_claimed", execute)
    task = asyncio.create_task(worker.run_forever())
    try:
        await until(lambda: len(started) == 3)
        await asyncio.sleep(0.06)
        assert len(claims) == 3
        gates[0].set()
        await until(lambda: len(started) == 4)
        assert len(claims) == 4
    finally:
        await stop(task)


@pytest.mark.asyncio
async def test_empty_queue_has_one_poller(monkeypatch):
    claim = AsyncMock(return_value=None)
    monkeypatch.setattr(worker, "claim_one", claim)
    task = asyncio.create_task(worker.run_forever())
    try:
        await asyncio.sleep(0.12)
        assert 1 <= claim.await_count <= 3
    finally:
        await stop(task)


@pytest.mark.asyncio
async def test_shutdown_during_commit_keeps_claim_owned(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    value = item()

    async def claim():
        entered.set()
        await release.wait()
        return value

    execute = AsyncMock()
    monkeypatch.setattr(worker, "claim_one", claim)
    monkeypatch.setattr(worker, "execute_claimed", execute)
    task = asyncio.create_task(worker.run_forever())
    try:
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    execute.assert_not_awaited()
    assert str(value.claim.run_id) in execution_health.faults


@pytest.mark.asyncio
async def test_shutdown_waits_for_all_slots_and_repeated_cancel(monkeypatch):
    entered, cleaning, release, done = [], [], asyncio.Event(), []
    monkeypatch.setattr(
        worker, "claim_one", AsyncMock(side_effect=lambda: item())
    )

    async def execute(value):
        entered.append(value)
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.append(value)
            await release.wait()
            done.append(value)

    monkeypatch.setattr(worker, "execute_claimed", execute)
    task = asyncio.create_task(worker.run_forever())
    try:
        await until(lambda: len(entered) == 3)
        task.cancel()
        await until(lambda: len(cleaning) == 3)
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert len(done) == 3


@pytest.mark.asyncio
async def test_uncertain_slot_stops_dispatch_and_drains_peers(monkeypatch):
    entered, stopped = [], []
    fail = asyncio.Event()
    monkeypatch.setattr(
        worker, "claim_one", AsyncMock(side_effect=lambda: item())
    )

    async def execute(value):
        entered.append(value)
        try:
            if len(entered) == 1:
                await fail.wait()
                raise ExecutionNeedsRecovery("lease lost")
            await asyncio.Event().wait()
        finally:
            stopped.append(value)

    monkeypatch.setattr(worker, "execute_claimed", execute)
    task = asyncio.create_task(worker.run_forever())
    await until(lambda: len(entered) == 3)
    fail.set()
    with pytest.raises(ExecutionNeedsRecovery):
        await asyncio.wait_for(task, 2)
    assert len(entered) == len(stopped) == 3


@pytest.mark.parametrize("value", [0, -1, 1.5, "bad"])
def test_invalid_concurrency_rejected(value):
    with pytest.raises(service_settings.ConfigurationError):
        service_settings.load_settings(
            config={"AGENT_WORKER_CONCURRENCY": value}, environ={}
        )


def test_concurrency_configuration_precedence():
    assert (
        service_settings.load_settings(
            config={}, environ={}
        ).commands.agent_worker_concurrency
        == 1
    )
    assert (
        service_settings.load_settings(
            config={}, environ={"AGENT_WORKER_CONCURRENCY": "4"}
        ).commands.agent_worker_concurrency
        == 4
    )
    s = service_settings.load_settings(
        config={"AGENT_WORKER_CONCURRENCY": 2},
        environ={"AGENT_WORKER_CONCURRENCY": "4"},
    )
    assert s.commands.agent_worker_concurrency == 2
    assert s.summary()["agent_worker_concurrency"] == 2
