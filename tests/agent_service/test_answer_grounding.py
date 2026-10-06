"""Exact facts on the wire; evidence provenance is not semantic truth verification."""

import json
import pytest
from dtest.agent_service.context import AgentContext
from dtest.agent_service.agents.analysis.execution.grounding import (
    FactReference,
    grounded_message,
    fact_value,
)
from dtest.agent_service.agents.analysis.agent_builders.conversation.agent import (
    reply_schema,
    build_agent,
)
from dtest.agent_service.agents.analysis.planning.catalog import AssetCatalog
from tests.agent_service.test_session_analysis_context import (
    saved_record,
    completed_analysis,
)


def context(record=None):
    return AgentContext(
        user_id="u",
        project_id="p",
        session_id="s",
        session_analysis_context=record or saved_record(value=109),
    )


def reply(
    message="아래는 확인된 관찰값입니다. 원인은 추가 검증이 필요합니다.",
    **changes,
):
    value = {
        "kind": "answer",
        "message": message,
        "plans": [],
        "grounding": {
            "scope": "analysis",
            "source_run_id": "r",
            "evidence_steps": ["stats"],
            "facts": [{"step_id": "stats", "path": ["mean"]}],
        },
    }
    value.update(changes)
    return reply_schema(AssetCatalog(), 5).model_validate(value)


def test_exact_numeric_fact_is_rendered_without_rounding_or_recalculation():
    value = grounded_message(reply(), context())
    assert (
        "109" in value
        and "{{fact:" not in value
        and "근거 Step: stats" in value
    )
    assert "원인이나 전체 분포가 검증되었다는 뜻은 아닙니다" in value
    assert "| stats.mean | 0.0109 |" in grounded_message(
        reply(), context(saved_record(value=0.0109))
    )


@pytest.mark.parametrize(
    "message",
    [
        "관찰값은 109입니다.",
        "이상치 비율은 1.09%입니다.",
        "## 1. 통계\n{{fact:0}}",
        "값 {{fact:1}}",
        "값 {{fact:-1}}",
        "값 {{fact:00}}",
        "값 {{fact:0}} 및 {{fact:9}}",
        "| 값 | 설명 |",
    ],
)
def test_no_copied_numbers_tables_or_legacy_fact_tokens(message):
    with pytest.raises(ValueError):
        grounded_message(reply(message), context())


@pytest.mark.parametrize(
    "changed",
    [
        "foreign_run",
        "foreign_owner",
        "failed",
        "incomplete",
        "omitted",
        "missing",
        "tool_name",
        "absent_path",
        "negative_index",
        "whole_object",
        "duplicate_citation",
        "missing_citation",
    ],
)
def test_references_cannot_cite_unknown_or_unusable_evidence(changed):
    c = context()
    value = reply()
    obs = c.session_analysis_context["payload"]["observations"][0]
    if changed == "foreign_run":
        value.grounding.source_run_id = "other"
    elif changed == "foreign_owner":
        c.session_analysis_context["owner"]["session_id"] = "other"
    elif changed == "failed":
        obs["status"] = "FAILED"
    elif changed == "incomplete":
        obs["incomplete"] = True
    elif changed == "omitted":
        obs.update(summary=None, summary_omitted=True)
    elif changed == "missing":
        obs["summary"] = None
    elif changed == "tool_name":
        value.grounding.evidence_steps = ["compute_statistics"]
    elif changed == "absent_path":
        value.grounding.facts[0].path = ["invented"]
    elif changed == "negative_index":
        obs["summary"] = [109]
        value.grounding.facts[0].path = [-1]
    elif changed == "whole_object":
        value.grounding.facts[0].path = []
    elif changed == "duplicate_citation":
        value.grounding.evidence_steps = ["stats", "stats"]
    elif changed == "missing_citation":
        value.grounding.evidence_steps = []
    with pytest.raises(ValueError):
        grounded_message(value, c)


def test_nested_typed_summary_exact_key_and_sample_limit_and_safe_markdown():
    c = context()
    obs = c.session_analysis_context["payload"]["observations"][0]
    obs["summary"] = {
        "type": "dict",
        "items": {
            "head": {
                "type": "list",
                "items": [{"0": "<script>|`"}],
                "truncated": True,
            },
            "shape": [10000, 8],
        },
        "truncated": False,
    }
    value = reply("실제 데이터 형태와 표본 값은 아래와 같습니다.")
    value.grounding.facts = [
        FactReference(step_id="stats", path=["head", 0, "0"]),
        FactReference(step_id="stats", path=["shape"]),
    ]
    text = grounded_message(value, c)
    assert "[10000, 8]" in text and "&lt;script&gt;&#124;&#96;" in text
    assert "일부 관찰이 생략되거나 제한" in text and "<script>" not in text
    with pytest.raises(ValueError):
        fact_value(obs, ["head", "0", "0"])


def test_faq_can_keep_numbers_without_claiming_analysis_and_context_disable_remains_supported():
    value = reply(
        "HTTP 404는 리소스를 찾지 못했다는 뜻입니다.",
        grounding={"scope": "general"},
    )
    assert grounded_message(value, context()) == value.message
    assert (
        grounded_message(
            reply("FAQ 404", grounding=None), context(), max_chars=0
        )
        == "FAQ 404"
    )
    with pytest.raises(ValueError):
        grounded_message(reply(grounding=None), context())
    with pytest.raises(ValueError):
        grounded_message(
            reply(grounding={"scope": "general", "evidence_steps": ["stats"]}),
            context(),
        )


def test_failed_analysis_keeps_partial_success_distinct_from_whole_success():
    c = context()
    c.session_analysis_context["payload"]["status"] = "analysis_failed"
    assert "이전 분석은 실패" in grounded_message(reply(), c)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["prompt_json", "provider_json_schema"])
async def test_production_create_agent_corrects_numbers_and_selectors_in_both_strategies(
    mode,
):
    import httpx
    from langchain_openai import ChatOpenAI

    calls = []

    async def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        message = (
            "관찰값은 999입니다."
            if len(calls) == 1
            else "실제 값은 아래와 같습니다. 원인은 확인되지 않았습니다."
        )
        value = reply(message).model_dump()
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
                            "content": json.dumps(value, ensure_ascii=False),
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    try:
        agent = build_agent(
            ChatOpenAI(
                model="test",
                api_key="test",
                base_url="http://llm.invalid/v1",
                http_async_client=client,
                max_retries=0,
            ),
            AssetCatalog(),
            structured_output_mode=mode,
        )
        value = await agent.ainvoke(
            {"request": "방금 통계를 설명해줘", "history": []},
            context=context(),
        )
        assert len(calls) == 2 and "109" in grounded_message(value, context())
        for call in calls:
            assert (
                sum(
                    m["role"] == "user"
                    and "previous_completed_session_analysis"
                    in str(m.get("content"))
                    for m in call["messages"]
                )
                == 1
            )
        assert any(
            "numeric" in str(m.get("content")) for m in calls[-1]["messages"]
        )
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_real_graph_publishes_same_rendered_values_in_sse_history_get_and_no_executor_submit(
    tmp_path, monkeypatch
):
    from uuid import uuid4

    runtime, executor, graph, config, state = await completed_analysis(
        tmp_path, monkeypatch
    )

    async def respond(value, c, datasets):
        payload = c.session_analysis_context["payload"]
        out = next(
            o for o in payload["observations"] if o["step_id"] == "outliers"
        )
        return reply_schema(runtime.catalog, 5)(
            kind="answer",
            message=(
                "이상치 후보 행은 아래와 같습니다. 원인은 추가 검증이 "
                "필요합니다."
            ),
            grounding={
                "scope": "analysis",
                "source_run_id": payload["source_run_id"],
                "evidence_steps": ["outliers"],
                "facts": [
                    {"step_id": "outliers", "path": ["outlier_indices"]}
                ],
            },
        )

    monkeypatch.setattr(runtime, "respond", respond)
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
            "user_request": "방금 결과를 설명하고 로드 설명은 빼줘",
        },
        config,
        durability="sync",
    )
    message = following["final_response"]["message"]
    assert "[5]" in message and "{{fact:" not in message
    assert following["history"][-1]["content"] == message
    assert (
        following["public_events"][-1]["envelope"]["data"]["content"][0][
            "text"
        ]
        == message
    )
    assert len(executor.calls) == 3 and following["execution_id"] is None
    assert (
        following["last_analysis_context"]["payload"]["step_outcomes"][-1][
            "status"
        ]
        == "SUCCEEDED"
    )


def test_step_outcomes_capture_skipped_failed_not_executed_and_budget_omission():
    from dtest.agent_service.runtime.session_analysis import (
        capture_analysis,
        bounded_analysis,
    )

    state = {"user_id": "u", "project_id": "p", "session_id": "s"}
    snapshot = {
        "run_id": "r",
        "document": {"goal": "分析"},
        "steps": [
            {"id": s, "tool_id": "tool"}
            for s in ["ok", "skip", "bad", "later"]
        ],
    }
    final = {
        "status": "analysis_failed",
        "execution_id": "e",
        "observations": [
            {"step_id": "ok", "status": "SUCCEEDED"},
            {"step_id": "bad", "status": "FAILED"},
        ],
        "skipped_steps": ["skip"],
    }
    record = capture_analysis(state, snapshot, final, 16000)
    assert [o["status"] for o in record["payload"]["step_outcomes"]] == [
        "SUCCEEDED",
        "SKIPPED",
        "FAILED",
        "NOT_EXECUTED",
    ]
    payload = record["payload"]
    payload["step_outcomes"] *= 1000
    bounded = bounded_analysis(payload, 2048)
    assert (
        bounded["omitted_step_outcomes"] > 0
        and len(json.dumps(bounded, ensure_ascii=False, separators=(",", ":")))
        <= 2048
    )


@pytest.mark.asyncio
async def test_exhausted_correction_never_publishes_unverified_numeric_reply():
    import httpx
    from langchain_openai import ChatOpenAI
    from dtest.agent_service.middleware.prompt_json import (
        StructuredResponseError,
    )

    calls = []

    async def handle(request):
        calls.append(request)
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
                                reply("관찰값은 999입니다.").model_dump()
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
        agent = build_agent(
            ChatOpenAI(
                model="test",
                api_key="test",
                base_url="http://llm.invalid/v1",
                http_async_client=client,
                max_retries=0,
            ),
            AssetCatalog(),
        )
        with pytest.raises(StructuredResponseError):
            await agent.ainvoke(
                {"request": "방금 결과 설명"}, context=context()
            )
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_cached_agent_concurrent_sessions_render_their_own_facts():
    import asyncio, httpx
    from langchain_openai import ChatOpenAI

    async def handle(request):
        body = json.loads(request.content)
        evidence = next(
            json.loads(m["content"])["analysis"]
            for m in body["messages"]
            if m["role"] == "user"
            and "previous_completed_session_analysis" in m["content"]
        )
        await asyncio.sleep(0.01)
        value = reply().model_dump()
        value["grounding"]["source_run_id"] = evidence["source_run_id"]
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
                            "content": json.dumps(value),
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle)
    ) as client:
        agent = build_agent(
            ChatOpenAI(
                model="test",
                api_key="test",
                base_url="http://llm.invalid/v1",
                http_async_client=client,
                max_retries=0,
            ),
            AssetCatalog(),
        )
        from dataclasses import replace

        one = context()
        two = replace(
            context(saved_record(session="another", value=22)),
            session_id="another",
        )
        two.session_analysis_context["payload"]["source_run_id"] = "second"
        values = await asyncio.gather(
            agent.ainvoke({"request": "방금 결과 설명"}, context=one),
            agent.ainvoke({"request": "방금 결과 설명"}, context=two),
        )
        assert "| stats.mean | 109 |" in grounded_message(values[0], one)
        assert "| stats.mean | 22 |" in grounded_message(values[1], two)


def test_strict_fact_path_preserves_numeric_string_keys_and_rejects_bool_and_large_objects():
    from pydantic import ValidationError

    for value in (True, 1.0):
        with pytest.raises(ValidationError):
            FactReference(step_id="s", path=[value])
    obs = {
        "status": "SUCCEEDED",
        "summary": {"0": 7, "large": "x" * 801, "many": list(range(17))},
    }
    assert fact_value(obs, ["0"]) == "7"
    with pytest.raises(ValueError):
        fact_value(obs, [0])
    for path in (["large"], ["many"]):
        with pytest.raises(ValueError):
            fact_value(obs, path)


def test_normal_multicolumn_statistics_can_select_more_than_thirty_two_facts():
    c = context()
    c.session_analysis_context["payload"]["observations"][0]["summary"] = {
        f"column_{i}": i + 0.001 for i in range(44)
    }
    value = reply("여러 통계 항목의 실제 값은 아래 표에서 확인할 수 있습니다.")
    value.grounding.facts = [
        FactReference(step_id="stats", path=[f"column_{i}"]) for i in range(44)
    ]
    text = grounded_message(value, c)
    assert "| stats.column_43 | 43.001 |" in text
    assert text.count("| stats.column_") == 44


def test_rendered_answer_budget_rejects_large_valid_selector_expansion():
    c = context()
    c.session_analysis_context["payload"]["observations"][0]["summary"] = {
        "text": "a" * 200
    }
    value = reply("설명" * 3000)
    value.grounding.facts = [
        FactReference(step_id="stats", path=["text"]) for _ in range(100)
    ]
    with pytest.raises(ValueError, match="bounded text budget"):
        grounded_message(value, c)
