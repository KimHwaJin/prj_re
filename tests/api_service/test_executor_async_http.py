"""Real local HTTP/1.1 sockets: pooling, deadlines, delivery uncertainty."""

from dtest.application.runs import monitoring
from dtest.application.runs.monitoring import run_cancellable
from dtest.application.runs.policy import is_retryable
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from dtest.settings.agent import load_agent_settings
from dtest.infrastructure.executor import client as api
from dtest.contracts.execution import ExecutionNeedsRecovery


def settings(url="http://127.0.0.1:9", **changes):
    return replace(
        load_agent_settings(
            {"EXECUTOR_BASE_URL": url, "EXECUTOR_SUBMIT_ENABLED": "true"}
        ),
        **changes,
    )


def receipt():
    return {
        "execution_id": "execution-1",
        "operation": {"operation_id": "operation-1", "steps": []},
        "state": {"status": "QUEUED", "version": 0},
    }


@asynccontextmanager
async def local_server(handle):
    stats = SimpleNamespace(connections=0, requests=[], active=0, peak=0)
    tasks = set()
    writers = set()

    async def connection(reader, writer):
        task = asyncio.current_task()
        tasks.add(task)
        writers.add(writer)
        stats.connections += 1
        try:
            while True:
                try:
                    header = await reader.readuntil(b"\r\n\r\n")
                except (asyncio.IncompleteReadError, ConnectionResetError):
                    return
                lines = header.decode().split("\r\n")
                method, path, _ = lines[0].split(" ")
                headers = dict(
                    line.split(": ", 1) for line in lines[1:] if ": " in line
                )
                length = int(
                    next(
                        (
                            v
                            for k, v in headers.items()
                            if k.lower() == "content-length"
                        ),
                        0,
                    )
                )
                raw = await reader.readexactly(length)
                request = {
                    "method": method,
                    "path": path,
                    "body": json.loads(raw) if raw else None,
                    "raw": raw,
                }
                stats.requests.append(request)
                stats.active += 1
                stats.peak = max(stats.peak, stats.active)
                try:
                    result = await handle(request)
                finally:
                    stats.active -= 1
                if result is None:
                    return  # accepted, then lost response
                status, body = result
                encoded = (
                    body
                    if isinstance(body, bytes)
                    else json.dumps(body).encode()
                )
                writer.write(
                    f"HTTP/1.1 {status} Test\r\nContent-Length: {len(encoded)}\r\nContent-Type: application/json\r\n\r\n".encode()
                    + encoded
                )
                await writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            writers.discard(writer)
            tasks.discard(task)

    server = await asyncio.start_server(connection, "127.0.0.1", 0)
    stats.url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    try:
        yield stats
    finally:
        server.close()
        await server.wait_closed()
        for writer in list(writers):
            writer.close()
        owned = list(tasks)
        for task in owned:
            task.cancel()
        await asyncio.gather(*owned, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["", "/proxy/executor/", "/gateway"])
async def test_all_operations_use_one_connection_and_close_with_owner(prefix):
    async def handle(request):
        return 200, receipt()

    async with local_server(handle) as server:
        cfg = settings(server.url + prefix)
        client = api.ExecutorClient(cfg)
        async with client:
            for method in [
                api.submit_execution_start,
                api.submit_execution_continue,
                api.submit_execution_finish,
                api.submit_execution_cancel,
                api.submit_execution_artifact,
            ]:
                args = (
                    (cfg, {"idempotency_key": "same-key"})
                    if method is api.submit_execution_start
                    else (cfg, "execution-1", {"idempotency_key": "same-key"})
                )
                await method(*args, client=client)
            for method in [
                api.get_execution,
                api.get_execution_result,
                api.get_execution_notebook,
            ]:
                await method(cfg, "execution-1", client=client)
            assert server.connections == 1
            assert [r["method"] for r in server.requests] == ["POST"] * 5 + [
                "GET"
            ] * 3
            expected = ["/api/v1/executions"] + [
                "/api/v1/executions/execution-1" + suffix
                for suffix in (
                    "/operations",
                    "/finalize",
                    "/cancel",
                    "/artifacts",
                    "",
                    "/result",
                    "/notebook",
                )
            ]
            assert [
                request["path"].split("?", 1)[0] for request in server.requests
            ] == [prefix.rstrip("/") + path for path in expected]
            assert "view=FULL" in server.requests[-1]["path"]
        assert client.http.is_closed
        with pytest.raises(RuntimeError, match="not open"):
            await api.get_execution(cfg, "execution-1", client=client)


@pytest.mark.asyncio
async def test_max_connections_bounds_concurrent_requests_and_reuses_pool():
    entered = asyncio.Event()
    release = asyncio.Event()
    seen = 0

    async def handle(request):
        nonlocal seen
        seen += 1
        if seen == 2:
            entered.set()
        await release.wait()
        return 200, {}

    async with local_server(handle) as server:
        cfg = settings(server.url, executor_http_max_connections=2)
        async with api.ExecutorClient(cfg) as client:
            calls = [
                asyncio.create_task(
                    api.get_execution(cfg, str(i), client=client)
                )
                for i in range(6)
            ]
            try:
                await asyncio.wait_for(entered.wait(), 2)
                await asyncio.sleep(0.03)
                assert server.peak == 2 and len(server.requests) == 2
            finally:
                release.set()
            await asyncio.gather(*calls)
            assert (
                server.connections == 2
                and len(server.requests) == 6
                and server.peak == 2
            )


@pytest.mark.asyncio
async def test_pool_timeout_is_known_unsent_and_has_no_server_side_effect():
    entered = asyncio.Event()
    release = asyncio.Event()

    async def handle(request):
        entered.set()
        await release.wait()
        return 200, {}

    async with local_server(handle) as server:
        cfg = settings(
            server.url,
            executor_http_max_connections=1,
            executor_http_pool_timeout_seconds=0.04,
        )
        async with api.ExecutorClient(cfg) as client:
            first = asyncio.create_task(
                api.get_execution(cfg, "hold", client=client)
            )
            try:
                await asyncio.wait_for(entered.wait(), 2)
                with pytest.raises(api.ExecutorSubmitError) as error:
                    with api.submission_scope():
                        await api.submit_execution_start(
                            cfg, {"idempotency_key": "not-sent"}, client=client
                        )
                assert is_retryable(error.value)
                assert len(server.requests) == 1
            finally:
                release.set()
                await first


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_deadline_stops_slow_response_without_waiting_for_external_job(
    method,
):
    entered = asyncio.Event()

    async def handle(request):
        entered.set()
        await asyncio.Event().wait()

    async with local_server(handle) as server:
        cfg = settings(server.url, executor_timeout_seconds=0.08)
        async with api.ExecutorClient(cfg) as client:
            expected = (
                api.ExecutorOutcomeUnknown
                if method == "POST"
                else api.ExecutorSubmitError
            )
            async with asyncio.timeout(1):
                with pytest.raises(expected):
                    await client.request(
                        method,
                        server.url,
                        {"idempotency_key": "slow"}
                        if method == "POST"
                        else None,
                    )
            assert entered.is_set() and len(server.requests) == 1


@pytest.mark.asyncio
async def test_lost_receipt_never_retries_automatically_and_explicit_replay_keeps_body():
    jobs = {}

    async def handle(request):
        key = request["body"]["idempotency_key"]
        if key not in jobs:
            jobs[key] = request["raw"]
            return None
        assert jobs[key] == request["raw"]
        return 202, receipt()

    async with local_server(handle) as server:
        cfg = settings(server.url)
        payload = {
            "idempotency_key": "task:operation:2",
            "expected_version": 4,
            "spec": {"steps": []},
        }
        async with api.ExecutorClient(cfg) as client:
            with pytest.raises(api.ExecutorOutcomeUnknown):
                await api.submit_execution_continue(
                    cfg, "execution-1", payload, client=client
                )
            assert len(server.requests) == len(jobs) == 1
            # Simulate an explicit reconciliation replay with the exact original
            # body. The client itself performs no retries after ambiguity.
            result = await api.submit_execution_continue(
                cfg, "execution-1", payload, client=client
            )
            assert result["body"]["execution_id"] == "execution-1"
            assert len(server.requests) == 2 and len(jobs) == 1
            assert server.requests[0]["raw"] == server.requests[1]["raw"]


@pytest.mark.asyncio
@pytest.mark.parametrize("during_http", [True, False])
async def test_cancel_during_or_just_after_post_remains_recovery_when_watcher_wins(
    monkeypatch, during_http
):
    entered = asyncio.Event()
    cancel = asyncio.Event()

    async def handle(request):
        if during_http:
            entered.set()
            await asyncio.Event().wait()
        return 202, receipt()

    async def watcher(*args):
        await cancel.wait()
        return True

    monkeypatch.setattr(monitoring, "wait_for_cancellation", watcher)
    async with local_server(handle) as server:
        cfg = settings(server.url)
        async with api.ExecutorClient(cfg) as client:

            async def graph():
                await api.submit_execution_start(
                    cfg, {"idempotency_key": "cancel"}, client=client
                )
                entered.set()
                await asyncio.Event().wait()

            task = asyncio.create_task(run_cancellable(uuid4(), graph()))
            await asyncio.wait_for(entered.wait(), 2)
            cancel.set()
            with pytest.raises(ExecutionNeedsRecovery):
                await asyncio.wait_for(task, 2)
            assert len(server.requests) == 1


@pytest.mark.asyncio
async def test_successful_post_then_local_failure_is_not_a_blind_retry():
    async def handle(request):
        return 202, receipt()

    async with local_server(handle) as server:
        cfg = settings(server.url)
        async with api.ExecutorClient(cfg) as client:
            with pytest.raises(api.ExecutorOutcomeUnknown):
                with api.submission_scope():
                    await api.submit_execution_start(
                        cfg, {"idempotency_key": "saved"}, client=client
                    )
                    raise ValueError("local persistence failed")
            assert len(server.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,body,error,retryable",
    [
        (400, {"secret": "must-not-leak"}, api.ExecutorSubmitError, False),
        (429, {}, api.ExecutorSubmitError, True),
        (500, {}, api.ExecutorOutcomeUnknown, False),
        (409, {}, api.ExecutorOutcomeUnknown, False),
        (307, {}, api.ExecutorOutcomeUnknown, False),
        (200, b"not json", api.ExecutorOutcomeUnknown, False),
        (202, {}, api.ExecutorOutcomeUnknown, False),
        (204, b"", api.ExecutorOutcomeUnknown, False),
    ],
)
async def test_rejections_and_invalid_receipts_are_classified_without_body_leak(
    status, body, error, retryable
):
    async def handle(request):
        return status, body

    async with local_server(handle) as server:
        cfg = settings(server.url)
        async with api.ExecutorClient(cfg) as client:
            with pytest.raises(error) as caught:
                with api.submission_scope():
                    await api.submit_execution_start(
                        cfg, {"idempotency_key": "test"}, client=client
                    )
            assert "must-not-leak" not in str(caught.value)
            if isinstance(caught.value, api.ExecutorSubmitError):
                assert is_retryable(caught.value) == retryable
            assert len(server.requests) == 1


@pytest.mark.asyncio
async def test_response_size_is_bounded():
    async def handle(request):
        return 200, {"data": "x" * 100}

    async with local_server(handle) as server:
        cfg = settings(server.url, executor_http_max_response_bytes=32)
        async with api.ExecutorClient(cfg) as client:
            with pytest.raises(api.ExecutorSubmitError, match="byte limit"):
                await api.get_execution(cfg, "id", client=client)
            with pytest.raises(api.ExecutorOutcomeUnknown):
                await api.submit_execution_start(
                    cfg, {"idempotency_key": "large"}, client=client
                )


@pytest.mark.parametrize(
    "key,value",
    [
        ("EXECUTOR_HTTP_MAX_CONNECTIONS", "0"),
        ("EXECUTOR_HTTP_POOL_TIMEOUT_SECONDS", "-1"),
        ("EXECUTOR_HTTP_CONNECT_TIMEOUT_SECONDS", "nan"),
        ("EXECUTOR_HTTP_MAX_RESPONSE_BYTES", "-1"),
    ],
)
def test_limits_reject_invalid_central_settings(key, value):
    from dtest.settings.loader import load_settings, ConfigurationError

    with pytest.raises(ConfigurationError):
        load_settings(config={key: value}, environ={})


@pytest.mark.asyncio
async def test_get_cancel_propagates_without_claiming_remote_mutation():
    entered = asyncio.Event()

    async def handle(request):
        entered.set()
        await asyncio.Event().wait()

    async with local_server(handle) as server:
        cfg = settings(server.url)
        async with api.ExecutorClient(cfg) as client:

            async def invoke():
                with api.submission_scope():
                    return await api.get_execution(cfg, "id", client=client)

            task = asyncio.create_task(invoke())
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


@pytest.mark.asyncio
async def test_planning_api_runtime_does_not_open_executor_client(monkeypatch):
    from dtest.application.runs.runtime import AgentGraphRuntime
    import dtest.agent_service.agents.analysis.planning.graph as module
    from unittest.mock import Mock

    runtime = AgentGraphRuntime()
    from dtest.infrastructure.memory.store import runtime as store_runtime
    from langgraph.store.memory import InMemoryStore

    @asynccontextmanager
    async def open_store():
        yield InMemoryStore()

    monkeypatch.setattr(store_runtime, "open_store", open_store)
    dependency = SimpleNamespace(store=None)
    monkeypatch.setattr(
        runtime,
        "_load_graph_inputs",
        lambda: (dependency, settings(), "memory"),
    )
    executor = Mock(
        side_effect=AssertionError("Planning must not open Executor HTTP")
    )
    monkeypatch.setattr(api, "ExecutorClient", executor)
    build = Mock(return_value=object())
    monkeypatch.setattr(module, "build_planning_graph", build)
    async with runtime.open_graph() as graph:
        assert graph is build.return_value
    async with runtime.open_graph() as second:
        assert second is graph
    assert build.call_count == 1
    assert isinstance(dependency.store, InMemoryStore)
    executor.assert_not_called()
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_planning_api_runtime_build_failure_has_no_executor_resources(
    monkeypatch,
):
    from dtest.application.runs.runtime import AgentGraphRuntime
    import dtest.agent_service.agents.analysis.planning.graph as module

    runtime = AgentGraphRuntime()
    from dtest.infrastructure.memory.store import runtime as store_runtime
    from langgraph.store.memory import InMemoryStore

    @asynccontextmanager
    async def open_store():
        yield InMemoryStore()

    monkeypatch.setattr(store_runtime, "open_store", open_store)
    monkeypatch.setattr(
        runtime,
        "_load_graph_inputs",
        lambda: (SimpleNamespace(store=None), settings(), "memory"),
    )

    def fail(*args, **kwargs):
        raise ValueError("graph build failure")

    monkeypatch.setattr(module, "build_planning_graph", fail)
    with pytest.raises(ValueError):
        async with runtime.open_graph():
            pass
    assert runtime._graph is None and runtime._stack is None
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_current_graph_uses_native_client_and_releases_at_executor_wait(
    tmp_path,
):
    from dataclasses import replace
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command
    from dtest.devtools.analysis.runtime import local_runtime, local_input
    from dtest.agent_service.agents.analysis.planning.runtime import (
        PlanningRuntime,
    )
    from dtest.agent_service.agents.analysis.planning.graph import (
        build_planning_graph,
    )

    execution = str(uuid4())
    registrations = []

    class Bindings:
        async def register(self, **kwargs):
            registrations.append(kwargs)

    async def handle(request):
        body = {**receipt(), "execution_id": execution}
        body["operation"]["steps"] = [
            {"sequence": step["sequence"], "step_id": str(uuid4())}
            for step in request["body"]["operation"]["spec"]["steps"]
        ]
        return 202, body

    async with local_server(handle) as server:
        cfg = replace(
            local_runtime().settings,
            executor_base_url=server.url,
            executor_source_type="INLINE",
            executor_submit_enabled=True,
        )
        async with api.ExecutorClient(cfg) as client:
            runtime = PlanningRuntime(
                cfg, executor=client, bindings=Bindings()
            )
            graph = build_planning_graph(runtime, checkpointer=InMemorySaver())
            value = local_input(runtime, "quality review")
            config = {"configurable": {"thread_id": value["session_id"]}}
            state = await graph.ainvoke(value, config)
            plan = state["plan_views"][0]
            with api.submission_scope():
                waiting = await graph.ainvoke(
                    Command(
                        resume={
                            "resume": {
                                "action": "approve_plan",
                                "plan_id": plan["plan_id"],
                                "plan_revision": plan["plan_revision"],
                            }
                        }
                    ),
                    config,
                )
            assert (
                waiting["__interrupt__"][0].value["kind"] == "EXECUTOR_EVENT"
            )
            assert len(server.requests) == len(registrations) == 1
            assert waiting["execution_id"] == execution
            assert (
                server.requests[0]["body"]["idempotency_key"]
                == waiting["execution_command"]["idempotency_key"]
            )
            assert server.active == 0 and not client.http.is_closed
            assert (await graph.aget_state(config)).next == ("execution_wait",)


@pytest.mark.asyncio
async def test_completed_submission_and_cancel_in_same_turn_do_not_unlock(
    monkeypatch,
):
    completed = asyncio.Event()

    async def handle(request):
        return 202, receipt()

    async def watcher(*args):
        await completed.wait()
        return True

    monkeypatch.setattr(monitoring, "wait_for_cancellation", watcher)
    async with local_server(handle) as server:
        cfg = settings(server.url)
        async with api.ExecutorClient(cfg) as client:

            async def graph():
                await api.submit_execution_start(
                    cfg, {"idempotency_key": "race"}, client=client
                )
                completed.set()
                return {"execution_id": "execution-1"}

            with pytest.raises(ExecutionNeedsRecovery):
                await run_cancellable(uuid4(), graph())
            assert len(server.requests) == 1


@pytest.mark.asyncio
async def test_concurrent_rejected_request_cannot_clear_an_accepted_submission():
    accepted = asyncio.Event()
    rejected_entered = asyncio.Event()

    async def handle(request):
        if request["body"]["idempotency_key"] == "rejected":
            rejected_entered.set()
            await accepted.wait()
            return 429, {}
        await rejected_entered.wait()
        return 202, receipt()

    async with local_server(handle) as server:
        cfg = settings(server.url)
        async with api.ExecutorClient(cfg) as client:

            async def success():
                await api.submit_execution_start(
                    cfg, {"idempotency_key": "accepted"}, client=client
                )
                accepted.set()

            async def rejected():
                with pytest.raises(api.ExecutorSubmitError):
                    await api.submit_execution_start(
                        cfg, {"idempotency_key": "rejected"}, client=client
                    )

            with pytest.raises(api.ExecutorOutcomeUnknown):
                with api.submission_scope():
                    await asyncio.gather(rejected(), success())
                    raise ValueError("local failure after concurrent calls")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url", ["not-an-absolute-url", "ftp://invalid.example/path"]
)
async def test_invalid_url_is_rejected_before_delivery_without_recovery(url):
    cfg = settings(url)
    async with api.ExecutorClient(cfg) as client:
        with pytest.raises(api.ExecutorSubmitError) as error:
            with api.submission_scope():
                await api.submit_execution_start(
                    cfg, {"idempotency_key": "invalid-url"}, client=client
                )
        assert not is_retryable(error.value)
