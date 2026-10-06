"""Real API/SQL admission and checkpoint restart; explicit Executor/model doubles."""

import json
from dataclasses import replace
from uuid import uuid4, UUID
import pytest
from langgraph.checkpoint.memory import InMemorySaver

import dtest.settings.loader as service_settings
from tests.api_service.test_planning_api_postgres import (
    planning,
    test_config,
    submit,
    execute,
    read,
)
from dtest.application.runs.runtime import runtime as graph_runtime
from dtest.application.runs.projection import synchronize_executor_completion
from dtest.application.runs.graph_invocation import GraphInvocation
from dtest.contracts.events import EventContext, ExecutorEvent
from dtest.contracts.execution_repair import RepairInteractionEvent
from dtest.agent_service.agents.analysis.planning.graph import (
    build_planning_graph,
)
from tests.agent_service.test_agentic_repair import (
    make_runtime,
    PartialResultExecutor,
    approve,
)


@pytest.mark.asyncio
async def test_repair_admission_checksum_revision_restart_replay_and_public_projection(
    planning, tmp_path, monkeypatch
):
    h = planning
    import dtest.application.runs.projection as completion

    monkeypatch.setattr(completion, "get_session_factory", lambda: h.factory)
    settings = service_settings.get_settings()
    agent = replace(
        settings.agent,
        executor_submit_enabled=True,
        executor_source_type="INLINE",
        executor_shared_result_root=tmp_path,
    )
    monkeypatch.setattr(
        service_settings, "_snapshot", replace(settings, agent=agent)
    )
    executor = PartialResultExecutor(tmp_path)
    runtime, calls = await make_runtime(
        agent, executor, level=1, proposed_level=2
    )
    saver = InMemorySaver()
    graph = build_planning_graph(runtime, checkpointer=saver)
    graph_runtime.override_graph(graph)
    response = await submit(
        h,
        {"input": {"content": [{"type": "text", "text": "Transform values"}]}},
    )
    rid = response.json()["run_id"]
    await execute()
    run = await read(h, rid)
    view = run["interrupt"][0]["payload"]["plans"][0]
    assert (
        await submit(
            h,
            {
                "run_id": rid,
                "resume_token": run["resume_token"],
                "command": {
                    "resume": {
                        "action": "approve_plan",
                        "plan_id": view["plan_id"],
                        "plan_revision": 1,
                    }
                },
            },
        )
    ).status_code == 202
    await execute()

    async def deliver(event):
        state = (
            await graph.aget_state(
                {"configurable": {"thread_id": h.session_id}}
            )
        ).values
        ctx = EventContext(
            namespace="test",
            session_id=h.session_id,
            task_id=state["task_id"],
            execution_id=UUID(executor.id),
            command_id=uuid4(),
            event=ExecutorEvent.model_validate(event),
        )
        await GraphInvocation(graph, model_validator=None).executor_resume(ctx)
        await synchronize_executor_completion(ctx, graph)
        return ctx

    ctx = await deliver(executor.events[0])
    run = await read(h, rid)
    assert (
        run["status"] == "waiting_input"
        and run["interrupt"][0]["kind"] == "repair_review"
    )
    token = run["resume_token"]
    review = run["interrupt"][0]
    command = approve(review, escalation=True)
    body = {"run_id": rid, "resume_token": token, "command": command}
    bad_actions = [
        {**command["resume"], "revision": 2},
        {**command["resume"], "proposal_sha256": "0" * 64},
        {**command["resume"], "allow_policy_escalation": False},
        {**command["resume"], "code": "print(1)"},
    ]
    for index, action in enumerate(bad_actions):
        status = (
            await submit(h, {**body, "command": {"resume": action}})
        ).status_code
        assert status == (409 if index == 0 else 422)
    assert (await read(h, rid))["resume_token"] == token and len(
        executor.calls
    ) == 1
    await GraphInvocation(graph, model_validator=None).executor_resume(ctx)
    assert len(calls) == 1 and len(executor.calls) == 1
    # Rebuild nodes/cache over the existing checkpoint before approval; no private Python in public form.
    await graph_runtime.shutdown()
    graph_runtime.start()
    graph = build_planning_graph(runtime, checkpointer=saver)
    graph_runtime.override_graph(graph)
    assert (await submit(h, body, key="approved-repair")).status_code == 202
    await execute()
    run = await read(h, rid)
    assert run["status"] == "waiting_executor" and run["resume_token"] is None
    assert (await submit(h, body, key="approved-repair")).status_code == 202
    assert len(executor.calls) == 2
    await deliver(executor.events[1])
    assert executor.calls[-1][0].endswith("/finalize")
    await deliver(
        executor.event(
            "execution.completed", {"status": "SUCCEEDED", "error": None}
        )
    )
    final = await read(h, rid)
    assert (
        final["status"] == "success"
        and final["result"]["final_response"]["repair"]["attempts"] == 1
    )
    stream = await h.client.get(
        h.path + "/" + rid + "/stream",
        headers={"X-User-Id": h.user["user_id"]},
    )
    events = [
        json.loads(line[6:])
        for line in stream.text.splitlines()
        if line.startswith("data: ")
    ]
    repairs = [
        e
        for e in events
        if e.get("type") == "interaction.opened"
        and e.get("data", {}).get("kind") == "repair_review"
    ]
    assert len(repairs) == 1
    RepairInteractionEvent.model_validate(repairs[0])
    assert (
        "def repair_transform" not in stream.text
        and "code_sha256" not in stream.text
    )
    assert executor.globals["repair_load_calls"] == 1
