"""Transport-only fixture: real create_agent/middleware, fixed delay per model call.

No production factory/config modifications. All requests stay in MockTransport;
submitted Tool Python is never executed by this fixture.
"""

import asyncio
import json
import time
from importlib.resources import files

import httpx
from langchain_openai import ChatOpenAI

PREFERENCE = "앞으로 보고서는 비전문가가 이해하기 쉽게 작성해줘"


def choose(body, observation_profile="standard"):
    payload = json.loads(
        next(m["content"] for m in body["messages"] if m["role"] == "user")
    )
    references = []
    for message in body["messages"]:
        if message["role"] == "user":
            try:
                value = json.loads(message["content"])
                if isinstance(value, dict) and value.get("reference_type"):
                    references.append(value)
            except (ValueError, TypeError):
                pass
    memory = next(
        (v for v in references if v["reference_type"] == "project_memory"),
        None,
    )
    analysis = next(
        (
            v["analysis"]
            for v in references
            if v["reference_type"] == "previous_completed_session_analysis"
        ),
        None,
    )
    if "pending_decisions" in payload:
        from dtest.agent_service.agents.analysis.planning.testing import (
            mock_execution_role,
        )

        return (
            "review",
            mock_execution_role("review", payload).model_dump(),
            memory,
            analysis,
        )
    if "repair_context" in payload:
        from dtest.agent_service.agents.analysis.planning.testing import (
            mock_execution_role,
        )

        return (
            "repair",
            mock_execution_role("repair", payload).model_dump(),
            memory,
            analysis,
        )
    if "observations" in payload and "request" not in payload:
        from dtest.agent_service.agents.analysis.planning.testing import (
            mock_execution_role,
        )

        return (
            "report",
            mock_execution_role("report", payload).model_dump(),
            memory,
            analysis,
        )
    request = payload["request"]
    if request.startswith("[answer]"):
        result = {
            "kind": "answer",
            "message": "저장된 근거를 바탕으로 검토용 설명을 제공합니다.",
            "plans": [],
        }
        if analysis:
            result["grounding"] = {
                "scope": "analysis",
                "source_run_id": analysis["source_run_id"],
                "evidence_steps": [
                    o["step_id"]
                    for o in analysis["observations"]
                    if o["status"] == "SUCCEEDED"
                ],
                "fact_ids": [
                    k
                    for group in analysis.get("fact_catalog", {}).values()
                    for k in group
                ][:4],
            }
        if memory and memory["automatic_write"] and PREFERENCE in request:
            from dtest.contracts.project_memory import section_body

            old_text = section_body(
                memory["memory"]["content"], "report_preferences"
            )
            content = "보고서는 비전문가가 이해하기 쉽게 작성한다"
            if (
                old_text != content
                and "report_preferences"
                in memory["write_policy"]["editable_sections"]
            ):
                result["memory_updates"] = [
                    {
                        "section": "report_preferences",
                        "old_text": old_text,
                        "content": content,
                        "expected_version": memory["memory"]["version"],
                        "quote": PREFERENCE,
                        "intent": "preference_change",
                    }
                ]
        return "answer", result, memory, analysis
    if not any(m["role"] == "tool" for m in body["messages"]):
        return (
            "planning_select",
            {
                "kind": "planning",
                "message": "등록된 스킬을 확인합니다.",
                "plans": [],
                "skill_ids": ["data_quality_check"],
                "grounding": None,
            },
            memory,
            analysis,
        )
    document = json.loads(
        files("dtest.agent_service.agents.analysis.planning")
        .joinpath("fixtures/quality-review.json")
        .read_text()
    )
    from observation_scenarios import plan_document

    document = plan_document(document, observation_profile)
    datasets = payload.get("dataset_catalog", [])
    return (
        "planning_plan",
        {
            "kind": "plans",
            "message": "등록된 도구로 계획을 준비했습니다.",
            "grounding": None,
            "plans": [
                {
                    "definition": document,
                    "input_values": {"dataset": datasets[0]["dataset_id"]}
                    if datasets
                    else {},
                }
            ],
        },
        memory,
        analysis,
    )


def install_model_fixture(cfg, metrics, enabled):
    from dtest.agent_service.agents.analysis.planning import runtime
    from dtest.infrastructure.observability import diagnostics as diag

    clients = []

    async def handle(request):
        body = json.loads(request.content)
        role, value, memory, analysis = choose(
            body, cfg.get("observation_profile", "standard")
        )
        began = time.perf_counter()
        measured = enabled()
        trace = diag._current.get()
        await asyncio.sleep(
            cfg["model_delay_ms"] / 1000
            if measured and not metrics.get("prepare")
            else 0
        )
        if measured:
            metrics["models"].append(
                {
                    "role": role,
                    "run_id": trace.run_id if trace else None,
                    "start": began,
                    "end": time.perf_counter(),
                    "memory_reference": memory is not None,
                    "analysis_reference": analysis is not None,
                }
            )
        return httpx.Response(
            200,
            json={
                "id": "local-service-fixture",
                "object": "chat.completion",
                "created": 0,
                "model": "fixture",
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
                "usage": {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                },
            },
        )

    def model(settings):
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handle), trust_env=False
        )
        clients.append(client)
        return ChatOpenAI(
            model="fixture",
            api_key="fixture-not-a-secret",
            base_url="http://fixture.invalid/v1",
            http_async_client=client,
            max_retries=0,
            temperature=0,
        )

    runtime.create_chat_model = model
    # Client lifetime is owned by this diagnostic process. MockTransport opens no
    # network sockets. Keep clients reachable across cached Agent invocations.
    runtime._benchmark_fixture_clients = clients
