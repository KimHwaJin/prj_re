"""Real analysis graph: durable waits, event replay, and sync I/O ownership."""
import asyncio
from dataclasses import replace
from threading import Event
from uuid import UUID, uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent_service.agents.analysis.graph import build_analysis_workflow_graph
from agent_service.agents.analysis.tests.test_user_agent_graph import (
    FakeExecutionBindings, FakeExecutorSubmitter, ReportAgent, analysis_context,
    dependencies, select_candidate, settings, start_analysis,
)
from app.agent_worker.langgraph_adapter import LangGraphEventAdapter
from app.services.workflow_persistence import NullWorkflowStore
from app.worker import EventContext, ExecutorEvent


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["submit", "catalog"])
async def test_cancel_keeps_graph_owned_until_sync_io_finishes(tmp_path, boundary):
    entered = asyncio.Event()
    release, finished = Event(), Event()
    loop = asyncio.get_running_loop()
    deps, _, _ = dependencies()
    submitter, bindings = FakeExecutorSubmitter(), FakeExecutionBindings()

    def wait_for_io():
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5), "test did not release the I/O gate"
        finished.set()

    def submit(config, payload):
        wait_for_io()
        return submitter(config, payload)

    class Store(NullWorkflowStore):
        def save_catalog_workflow(self, **kwargs):
            wait_for_io()
            return "test-catalog"

    graph = build_analysis_workflow_graph(
        deps, settings(artifacts_root=tmp_path), checkpointer=InMemorySaver(),
        submit_execution_start=submit if boundary == "submit" else submitter,
        bindings=bindings, workflow_store=Store() if boundary == "catalog" else None,
    )
    config = await start_analysis(graph, f"cancel-{boundary}")
    if boundary == "submit":
        result = await graph.ainvoke(Command(resume=analysis_context()), config)
        await select_candidate(graph, config, result)
        command = Command(resume={"approved": True})
    else:
        command = Command(resume=analysis_context())
    task = asyncio.create_task(graph.ainvoke(command, config))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        # The event loop remains available, but the graph must still own its I/O.
        await asyncio.sleep(.03)
        assert not task.done()
        task.cancel()
        await asyncio.sleep(.03)
        assert not task.done()
        assert not finished.is_set()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert await asyncio.to_thread(finished.wait, 2)
    assert task.cancelled()
    assert bindings.registrations == []  # No next-node side effect after cancel.


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["SUCCEEDED", "FAILED"])
async def test_approval_to_executor_event_and_report_survives_rebuild(tmp_path, monkeypatch, status):
    deps, _, _ = dependencies()
    report = ReportAgent()
    deps = replace(deps, report_agent=report)
    submitter, bindings = FakeExecutorSubmitter(), FakeExecutionBindings()
    saver = InMemorySaver()
    app_settings = settings(artifacts_root=tmp_path)
    reads, uploads = [], []

    def read_results(_settings, state):
        reads.append(state["execution_id"])
        return [{"tool_id": "quality.profile", "step_id": "quality", "tool": "profile_data",
                 "role": "tool_execution", "result": {"status": status, "outputs": []}}]

    # Factory default parameters are bound at import time; intercept the HTTP
    # transport boundary, so real report construction still runs without network.
    def upload(url, payload, **kwargs):
        uploads.append(payload)
        return {"status_code": 202, "body": {}}

    monkeypatch.setattr("app.services.executor_client._post_json", upload)

    def build():
        return build_analysis_workflow_graph(
            deps, app_settings, checkpointer=saver,
            submit_execution_start=submitter, bindings=bindings,
            execution_result_reader=read_results,
        )

    graph = build()
    config = await start_analysis(graph, f"lifecycle-{status}")
    await graph.aupdate_state(config, {"project_system_prompt": "test project policy", "project_prompt_version": 4})
    result = await graph.ainvoke(Command(resume=analysis_context()), config)
    await select_candidate(graph, config, result)
    waiting = await graph.ainvoke(Command(resume={"approved": True}), config)
    assert waiting["__interrupt__"][0].value["kind"] == "EXECUTOR_EVENT"
    assert len(submitter.calls) == len(bindings.registrations) == 1
    assert reads == uploads == []
    assert report.calls == []

    # Simulate process-local graph replacement during the long external wait.
    graph = build()
    event = ExecutorEvent(
        event_id=uuid4(), execution_id=UUID(waiting["execution_id"]),
        event_type="execution.completed", event_sequence=1,
        schema_version="1.0", occurred_at="2026-09-28T00:00:00+00:00",
        payload={"state": {"status": status, "version": 2}},
    )
    context = EventContext(
        namespace="test", session_id=config["configurable"]["thread_id"],
        task_id=waiting["task_id"], execution_id=event.execution_id,
        command_id=uuid4(), event=event,
    )
    adapter = LangGraphEventAdapter(graph)
    await adapter(context)
    final = await graph.aget_state(config)
    assert not final.next
    assert final.values["execution_status"] == status
    assert final.values["ew_receipts"][str(context.command_id)] == str(event.event_id)
    assert final.values["project_system_prompt"] == "test project policy"
    assert len(report.calls) == len(uploads) == (1 if status == "SUCCEEDED" else 0)
    assert final.values["report_status"] == ("generated" if status == "SUCCEEDED" else "skipped_execution_failed")
    if status == "SUCCEEDED":
        assert final.values["analysis_report"]["content"].startswith("# 분석 리포트")
    await adapter(context)  # Receipt replay cannot re-run result/report/submit.
    assert len(reads) == len(submitter.calls) == len(bindings.registrations) == 1
    assert len(report.calls) == len(uploads) == (1 if status == "SUCCEEDED" else 0)
