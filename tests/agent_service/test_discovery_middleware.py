"""Exercise real create_agent metadata loops with bounded async model transport."""

import json
import httpx
import pytest
from langchain_openai import ChatOpenAI

from dtest.agent_service.agents.analysis.agent_builders.conversation.agent import (
    build_agent,
)
from dtest.agent_service.agents.analysis.planning.catalog import AssetCatalog
from dtest.agent_service.context import AgentContext


@pytest.mark.asyncio
async def test_repeated_skill_query_forces_answer_and_context_remains_isolated():
    calls = []

    async def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        if not any(m["role"] == "tool" for m in body["messages"]):
            message = {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "kind": "planning",
                        "message": "계획을 준비합니다.",
                        "skill_ids": ["data_quality_check"],
                        "plans": [],
                    }
                ),
            }
            reason = "stop"
        elif body.get("tools"):
            message = {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": f"query-{len(calls)}",
                        "type": "function",
                        "function": {
                            "name": "read_skill",
                            "arguments": '{"skill_id":"data_quality_check"}',
                        },
                    }
                ],
            }
            reason = "tool_calls"
        else:
            message = {
                "role": "assistant",
                "content": (
                    '{"kind":"answer","message":"아직 분석은 실행하지 '
                    '않았습니다.","plans":[]}'
                ),
            }
            reason = "stop"
        return httpx.Response(
            200,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 0,
                "model": "test",
                "choices": [
                    {"index": 0, "message": message, "finish_reason": reason}
                ],
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle)
    ) as client:
        model = ChatOpenAI(
            model="test",
            api_key="test",
            base_url="http://model.invalid/v1",
            max_retries=0,
            http_async_client=client,
        )
        agent = build_agent(model, AssetCatalog(), discovery_max_rounds=4)
        for i in range(2):
            result = await agent.ainvoke(
                {"request": "분석해줘"},
                context=AgentContext(project_system_prompt="PROJECT RULE"),
            )
            assert result.kind == "answer"
    assert (
        len(calls) == 6
    )  # selection/read, duplicate receipt, forced synthesis, per invocation
    for body in calls:
        assert body["messages"][0]["content"].count("PROJECT RULE") == 1
    assert all(not calls[i].get("tools") for i in (2, 5))
    assert "already returned" in calls[2]["messages"][-1]["content"]
