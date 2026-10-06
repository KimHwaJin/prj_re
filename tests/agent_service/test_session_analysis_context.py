"""Completed analysis must inform a later turn without rerunning its code."""

from copy import deepcopy
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from dtest.agent_service.agents.analysis.planning.graph import (
    build_planning_graph,
)
from dtest.agent_service.agents.analysis.agent_builders.conversation.agent import (
    reply_schema,
)
from tests.agent_service.test_agentic_execution import setup


async def completed_analysis(tmp_path, monkeypatch):
    runtime, executor, graph, config, state, deliver = await setup(
        tmp_path, monkeypatch
    )
    _, state = await deliver(executor.events[0])
    _, state = await deliver(executor.events[1])
    _, state = await deliver(
        executor.event(
            "execution.completed", {"status": "SUCCEEDED", "error": None}
        )
    )
    return runtime, executor, graph, config, state


@pytest.mark.asyncio
async def test_completed_results_reach_followup_after_graph_rebuild_without_executor_resubmission(
    tmp_path, monkeypatch
):
    runtime, executor, graph, config, state = await completed_analysis(
        tmp_path, monkeypatch
    )
    calls = []

    async def respond(value, context, datasets):
        calls.append(deepcopy(context.session_analysis_context))
        return reply_schema(runtime.catalog, 5)(
            kind="answer", message="방금 실제 결과를 설명합니다.", plans=[]
        )

    monkeypatch.setattr(runtime, "respond", respond)
    graph = build_planning_graph(runtime, checkpointer=graph.checkpointer)
    next_input = {
        k: state[k]
        for k in ("user_id", "project_id", "session_id", "model_selection")
    }
    next_input.update(
        run_id=str(uuid4()),
        user_request="방금 결과에서 이상치가 왜 나온 건지 설명해줘",
    )
    following = await graph.ainvoke(next_input, config, durability="sync")
    saved = calls[0]["payload"]
    assert saved["execution_id"] == executor.id
    assert saved["status"] == "analysis_completed"
    assert saved["observations"][-1]["summary"]["items"]["outlier_indices"][
        "items"
    ] == [5]
    assert saved["decisions"]["outlier_method"] == "iqr"
    assert saved["report"]["excerpt"]
    assert following["final_response"]["status"] == "answer"
    assert (
        following["observations"] == [] and following["execution_id"] is None
    )
    assert len(executor.calls) == 3
    assert following["last_analysis_context"] == state["last_analysis_context"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed_owner", ["user_id", "project_id", "session_id"]
)
async def test_analysis_context_is_not_supplied_to_another_owner_even_with_copied_checkpoint(
    tmp_path, monkeypatch, changed_owner
):
    runtime, executor, graph, config, state = await completed_analysis(
        tmp_path, monkeypatch
    )
    copied = deepcopy(state)
    copied[changed_owner] = str(uuid4())
    copied.update(
        run_id=str(uuid4()), user_request="이전에 했던 분석을 설명해줘"
    )

    async def respond(value, context, datasets):
        assert context.session_analysis_context is None
        return reply_schema(runtime.catalog, 5)(
            kind="answer",
            message="이 세션에서 확인된 이전 분석은 없습니다.",
            plans=[],
        )

    monkeypatch.setattr(runtime, "respond", respond)
    graph = build_planning_graph(runtime, checkpointer=InMemorySaver())
    following = await graph.ainvoke(
        copied,
        {"configurable": {"thread_id": copied["session_id"]}},
        durability="sync",
    )
    assert following["last_analysis_context"] is None


@pytest.mark.asyncio
async def test_new_calculation_requires_approval_and_starts_new_execution_with_data_load(
    tmp_path, monkeypatch
):
    from langgraph.types import Command
    from dtest.contracts.user_resume import resume_identity, resume_envelope
    from tests.agent_service.test_agentic_execution import LocalExecutor

    runtime, old, graph, config, state = await completed_analysis(
        tmp_path, monkeypatch
    )
    executor = LocalExecutor(tmp_path)
    runtime.executor = executor
    # The explicit mock suggests registered Tools; old Python globals are absent.
    following = await graph.ainvoke(
        {
            **{
                k: state[k]
                for k in (
                    "user_id",
                    "project_id",
                    "session_id",
                    "model_selection",
                )
            },
            "run_id": str(uuid4()),
            "user_request": "같은 데이터로 새로 이상치를 계산해줘",
        },
        config,
        durability="sync",
    )
    assert following["__interrupt__"][0].value["kind"] == "plan_review"
    assert not executor.calls and not executor.globals
    plan = following["plan_views"][0]
    command = {
        "resume": {
            "action": "approve_plan",
            "plan_id": plan["plan_id"],
            "plan_revision": plan["plan_revision"],
        }
    }
    boundary = following["__interrupt__"][0]
    identity = resume_identity(str(uuid4()), boundary.id, command)
    submitted = await graph.ainvoke(
        Command(resume={boundary.id: resume_envelope(identity, command)}),
        config,
        durability="sync",
    )
    assert submitted["execution_id"] == executor.id != old.id
    steps = executor.calls[0][1]["operation"]["spec"]["steps"]
    assert (
        steps[0]["lineage"]["tool_name"] == "data_load"
        and steps[0]["sequence"] == 0
    )
    assert (
        old.calls[0][1]["idempotency_key"]
        != executor.calls[0][1]["idempotency_key"]
    )


def saved_record(*, user="u", project="p", session="s", value=11):
    return {
        "schema_version": 1,
        "owner": {
            "user_id": user,
            "project_id": project,
            "session_id": session,
        },
        "payload": {
            "source_run_id": "r",
            "execution_id": "e",
            "status": "analysis_completed",
            "goal": "실제 결과 설명",
            "dataset_references": [
                {
                    "input_name": "dataset",
                    "dataset_id": "allowed",
                    "title": "확인된 데이터",
                }
            ],
            "decisions": {"method": "iqr"},
            "observations": [
                {
                    "step_id": "stats",
                    "tool_id": "compute_statistics",
                    "status": "SUCCEEDED",
                    "summary": {"mean": value},
                    "has_image": False,
                    "incomplete": False,
                }
            ],
            "report": {
                "status": "ready",
                "excerpt": "# 분석 결과\n\n확인된 결과입니다.",
            },
        },
    }


def test_budget_keeps_metrics_exact_or_marks_omission_and_can_be_reduced_without_new_execution():
    import json
    from dtest.agent_service.runtime.session_analysis import (
        bounded_analysis,
        analysis_for_owner,
    )

    record = saved_record()
    payload = record["payload"]
    payload["observations"] = [
        {
            **payload["observations"][0],
            "step_id": "step-" + str(i),
            "summary": {"metric": 123.456789, "large_text": "x" * 4000},
        }
        for i in range(30)
    ]
    payload["report"]["excerpt"] = '"\\\n결과' * 30000
    bounded = bounded_analysis(payload, 2048)
    assert (
        len(json.dumps(bounded, ensure_ascii=False, separators=(",", ":")))
        <= 2048
    )
    assert (
        bounded["report"]["truncated"] and bounded["omitted_observations"] > 0
    )
    assert bounded["observations"][-1]["step_id"] == "step-29"
    assert all(
        o["summary"] is None and o["summary_omitted"]
        for o in bounded["observations"]
    )
    record["payload"] = bounded_analysis(saved_record()["payload"], 64000)
    restored = analysis_for_owner(record, record["owner"], 2048)
    assert restored["payload"]["observations"][0]["summary"]["mean"] == 11
    assert analysis_for_owner(record, record["owner"], 0) is None
    # Caller state cannot be changed by trimming/reuse.
    restored["payload"]["observations"][0]["summary"]["mean"] = 999
    assert record["payload"]["observations"][0]["summary"]["mean"] == 11


@pytest.mark.parametrize("value", [-1, 1, 2047, 64001])
def test_invalid_context_budget_is_rejected(value):
    from dtest.settings.loader import load_settings, ConfigurationError

    with pytest.raises(ConfigurationError):
        load_settings(
            config={"AGENT_SESSION_ANALYSIS_MAX_CHARS": value}, environ={}
        )


def test_context_budget_yaml_precedence_and_disable():
    from dtest.settings.loader import load_settings

    assert (
        load_settings(
            config={"AGENT_SESSION_ANALYSIS_MAX_CHARS": 2048},
            environ={"AGENT_SESSION_ANALYSIS_MAX_CHARS": "32000"},
        ).agent.agent_session_analysis_max_chars
        == 2048
    )
    assert (
        load_settings(
            config={}, environ={"AGENT_SESSION_ANALYSIS_MAX_CHARS": "0"}
        ).agent.agent_session_analysis_max_chars
        == 0
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["prompt_json", "provider_json_schema"])
async def test_shared_middleware_isolates_concurrent_evidence_and_reapplies_it_on_retry(
    mode,
):
    import asyncio, json, httpx
    from langchain_openai import ChatOpenAI
    from pydantic import BaseModel
    from langchain_core.messages import HumanMessage
    from dtest.agent_service.context import AgentContext
    from dtest.agent_service.factory import build_role_agent, json_output
    from dtest.agent_service.middleware import (
        SessionAnalysisMiddleware,
        ProjectPromptMiddleware,
    )

    class Reply(BaseModel):
        count: int

    calls = {11: [], 22: []}
    both = asyncio.Event()

    def validate(response, request):
        original = json.loads(
            next(
                m.content
                for m in request.messages
                if isinstance(m, HumanMessage)
            )
        )
        if response.count != original["expected_count"]:
            raise ValueError("Use the supplied exact count")

    async def handle(request):
        body = json.loads(request.content)
        raw = next(
            m["content"] for m in body["messages"] if m["role"] == "user"
        )
        number = json.loads(raw)["expected_count"]
        calls[number].append(body)
        if all(calls.values()):
            both.set()
        await asyncio.wait_for(both.wait(), 3)
        return httpx.Response(
            200,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 0,
                "model": "test",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "count": 0
                                    if len(calls[number]) == 1
                                    else number
                                }
                            ),
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle)
    ) as client:
        model = ChatOpenAI(
            model="test",
            api_key="test",
            base_url="http://test/v1",
            max_retries=0,
            http_async_client=client,
        )
        agent = build_role_agent(
            model,
            name="context-test",
            system_prompt="ROLE",
            tools=[],
            middleware=[
                ProjectPromptMiddleware(),
                SessionAnalysisMiddleware(),
            ],
            output_type=Reply,
            decode=json_output(Reply),
            validate_response=validate,
            structured_output_mode=mode,
        )
        results = await asyncio.gather(
            *(
                agent.ainvoke(
                    {"expected_count": n},
                    context=AgentContext(
                        user_id="u",
                        project_id="p",
                        session_id=str(n),
                        project_system_prompt="PROJECT",
                        session_analysis_context=saved_record(
                            session=str(n), value=n
                        ),
                    ),
                )
                for n in (11, 22)
            )
        )
    assert [r.count for r in results] == [11, 22]
    for n, requests in calls.items():
        assert len(requests) == 2
        for body in requests:
            references = [
                json.loads(m["content"])
                for m in body["messages"]
                if m["role"] == "user" and '"reference_type"' in m["content"]
            ]
            assert len(references) == 1
            assert references[0]["analysis"]["observations"][0]["summary"] == {
                "mean": n
            }
            assert body["messages"][0]["content"].count("PROJECT") == 1
            assert "reference_type" not in body["messages"][0]["content"]


@pytest.mark.asyncio
async def test_failed_analysis_preserves_successful_evidence_but_does_not_claim_success(
    tmp_path, monkeypatch
):
    runtime, executor, graph, config, state, deliver = await setup(
        tmp_path, monkeypatch, single=True, missing=True
    )
    _, state = await deliver(executor.events[0])
    _, state = await deliver(
        executor.event(
            "execution.completed",
            {"status": "FAILED", "error": {"message": "File missing"}},
        )
    )
    record = state["last_analysis_context"]["payload"]
    assert record["status"] == "analysis_failed"
    assert all(o["status"] != "SUCCEEDED" for o in record["observations"])
    assert all(o["summary"] is None for o in record["observations"])


@pytest.mark.asyncio
async def test_disabled_context_does_not_change_execution_report(
    tmp_path, monkeypatch
):
    from dataclasses import replace

    runtime, executor, graph, config, state, deliver = await setup(
        tmp_path, monkeypatch
    )
    runtime.settings = replace(
        runtime.settings, agent_session_analysis_max_chars=0
    )
    _, state = await deliver(executor.events[0])
    _, state = await deliver(executor.events[1])
    _, state = await deliver(
        executor.event(
            "execution.completed", {"status": "SUCCEEDED", "error": None}
        )
    )
    assert state["last_analysis_context"] is None
    assert state["final_response"]["report"]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["prompt_json", "provider_json_schema"])
@pytest.mark.parametrize(
    "invalid",
    [
        "tool_name_as_evidence",
        "missing_required_evidence",
        "wrong_value",
        "duplicate_decision",
    ],
)
async def test_execution_review_corrects_invalid_evidence_and_values_inside_middleware(
    mode, invalid
):
    import json, httpx
    from langchain_openai import ChatOpenAI
    from dtest.agent_service.agents.analysis.agent_builders.execution_review.agent import (
        build_agent,
    )
    from dtest.agent_service.context import AgentContext

    calls = []
    choices = [
        {
            "decision_id": "method",
            "value": "iqr",
            "reason": "실제 통계에 근거한 선택",
            "evidence_steps": ["profile", "statistics"],
        }
    ]

    async def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        selected = deepcopy(choices)
        if len(calls) == 1:
            if invalid == "tool_name_as_evidence":
                selected[0]["evidence_steps"] = ["compute_statistics"]
            elif invalid == "missing_required_evidence":
                selected[0]["evidence_steps"] = ["statistics"]
            elif invalid == "wrong_value":
                selected[0]["value"] = "imagined_method"
            else:
                selected.append(deepcopy(selected[0]))
        return httpx.Response(
            200,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 0,
                "model": "test",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "choices": selected,
                                    "needs_user_input": False,
                                    "message": "실제 결과를 확인했습니다.",
                                }
                            ),
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle)
    ) as client:
        model = ChatOpenAI(
            model="test",
            api_key="test",
            base_url="http://test/v1",
            max_retries=0,
            http_async_client=client,
        )
        result = await build_agent(model, structured_output_mode=mode).ainvoke(
            {
                "pending_decisions": [
                    {
                        "id": "method",
                        "after_steps": ["profile", "statistics"],
                        "output_schema": {"enum": ["iqr", "zscore"]},
                    }
                ],
                "observations": [
                    {
                        "step_id": "profile",
                        "tool_id": "profile_data",
                        "status": "SUCCEEDED",
                        "incomplete": False,
                    },
                    {
                        "step_id": "statistics",
                        "tool_id": "compute_statistics",
                        "status": "SUCCEEDED",
                        "incomplete": False,
                    },
                ],
            },
            context=AgentContext(project_system_prompt="PROJECT RULE"),
        )
    assert len(calls) == 2 and result.choices[0].value == "iqr"
    assert all(
        c["messages"][0]["content"].count("PROJECT RULE") == 1 for c in calls
    )
    assert "validation" in calls[1]["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_exhausted_review_validation_preserves_hitl_without_auto_execution(
    tmp_path, monkeypatch
):
    from dtest.agent_service.middleware.prompt_json import (
        StructuredResponseError,
    )

    runtime, executor, graph, config, state, deliver = await setup(
        tmp_path, monkeypatch
    )
    original = runtime.execution_role

    async def role(name, *args):
        if name == "review":
            raise StructuredResponseError("Review cannot cite an allowed Step")
        return await original(name, *args)

    monkeypatch.setattr(runtime, "execution_role", role)
    _, state = await deliver(executor.events[0])
    assert (
        state["execution_phase"] == "decision_wait"
        and len(executor.calls) == 1
    )
    assert state["execution_review_validation_error"]
    assert all(
        not d["has_value"]
        for d in state["decision_review"]["payload"]["decisions"]
    )
    assert state["decision_review"]["status"] == "open"
