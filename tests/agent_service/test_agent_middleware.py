"""Real current create_agent roles, async transport and isolated project context."""

import asyncio
import importlib
import json
from unittest.mock import AsyncMock
from uuid import uuid4
from typing import TypedDict
import httpx
import pytest
from pydantic import BaseModel
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command, interrupt
from langgraph.graph import StateGraph, START, END
from dtest.agent_service.context import AgentContext
from dtest.agent_service.factory import build_role_agent, json_output
from dtest.agent_service.middleware import ProjectPromptMiddleware
from dtest.agent_service.agents.analysis.planning.catalog import AssetCatalog
from tests.agent_service.model_helpers import chat_model


def response(content):
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
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
        },
    )


ROLES = [
    (
        "conversation",
        json.dumps(
            {
                "kind": "answer",
                "message": "General answer",
                "plans": [],
                "grounding": {"scope": "general"},
            }
        ),
    ),
    (
        "plan_revision",
        json.dumps(
            {
                "kind": "clarification",
                "message": "Please clarify the goal",
                "plans": [],
            }
        ),
    ),
    (
        "execution_review",
        json.dumps(
            {"choices": [], "needs_user_input": False, "message": "Reviewed"}
        ),
    ),
    (
        "execution_report",
        json.dumps(
            {"markdown": "# Interpretation", "evidence_steps": ["profile"]}
        ),
    ),
    (
        "execution_repair",
        json.dumps(
            {
                "can_repair": False,
                "summary": "Cannot repair",
                "reason": "No correction",
                "evidence_steps": [],
            }
        ),
    ),
]


def role_payload(role):
    if role == "conversation":
        return {
            "request": "hello",
            "history": [],
            "dataset_catalog": [],
            "available_skills": [],
        }
    if role == "plan_revision":
        return {"original_request": "hello", "previous_plans": []}
    if role == "execution_review":
        return {"pending_decisions": [], "observations": []}
    if role == "execution_report":
        return {
            "observations": [
                {
                    "step_id": "profile",
                    "tool_id": "profile_data",
                    "status": "SUCCEEDED",
                }
            ]
        }
    return {
        "repair_context": {"repair_authorized_level": 0, "failed_step_ids": []}
    }


def role_builder(role, model, mode="prompt_json"):
    module = importlib.import_module(
        "dtest.agent_service.agents.analysis.agent_builders." + role + ".agent"
    )
    args = [model]
    if role in {"conversation", "plan_revision"}:
        args.append(AssetCatalog())
    return module.build_agent(*args, structured_output_mode=mode)


@pytest.mark.asyncio
@pytest.mark.parametrize("role,content", ROLES)
@pytest.mark.parametrize("mode", ["prompt_json", "provider_json_schema"])
async def test_every_current_role_uses_async_agent_and_project_prompt(
    role, content, mode
):
    calls = []

    async def handle(request):
        calls.append(json.loads(request.content))
        return response(content)

    def no_sync(_):
        raise AssertionError("sync HTTP called")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as ac:
        with httpx.Client(transport=httpx.MockTransport(no_sync)) as sc:
            model = ChatOpenAI(
                model="test",
                api_key="test",
                base_url="http://llm.invalid/v1",
                max_retries=0,
                http_async_client=ac,
                http_client=sc,
            )
            agent = role_builder(role, model, mode)
            result = await agent.ainvoke(
                role_payload(role),
                context=AgentContext(
                    user_id="private-user-id",
                    project_id="private-project-id",
                    session_id="private-session-id",
                    project_system_prompt="PROJECT RULE",
                    project_prompt_version=4,
                ),
            )
    assert result is not None and agent.agent.checkpointer is False
    assert (
        len(calls) == 1
        and calls[0]["messages"][0]["content"].count("PROJECT RULE") == 1
    )
    assert "private-project-id" not in json.dumps(calls[0]["messages"])
    assert ("response_format" in calls[0]) == (mode == "provider_json_schema")


@pytest.mark.asyncio
async def test_shared_agent_context_is_isolated_across_concurrent_projects():
    entered = asyncio.Event()
    calls = []

    class Backend:
        async def ainvoke(self, messages):
            calls.append(messages)
            if len(calls) == 2:
                entered.set()
            await asyncio.wait_for(entered.wait(), 2)
            return AIMessage(content=ROLES[0][1])

    agent = role_builder("conversation", chat_model(Backend()))
    await asyncio.gather(
        *(
            agent.ainvoke(
                role_payload("conversation"),
                context=AgentContext(project_system_prompt=name),
            )
            for name in ["PROJECT_A", "PROJECT_B"]
        )
    )
    assert len(calls) == 2
    assert sum("PROJECT_A" in m[0].content for m in calls) == 1
    assert sum("PROJECT_B" in m[0].content for m in calls) == 1


class Answer(BaseModel):
    answer: str


@pytest.mark.asyncio
@pytest.mark.parametrize("succeeds", [True, False])
async def test_json_retry_is_bounded_and_reapplies_prompt_without_duplication(
    succeeds,
):
    calls = []
    seen = []

    class Backend:
        async def ainvoke(self, messages):
            calls.append(messages)
            return AIMessage(
                content='{"answer":"ok"}'
                if succeeds and len(calls) == 3
                else "invalid"
            )

    class Observe(AgentMiddleware):
        async def awrap_model_call(self, request, handler):
            seen.append(request.runtime.context)
            return await handler(request)

    agent = build_role_agent(
        chat_model(Backend()),
        name="retry",
        system_prompt="role",
        tools=[],
        middleware=[ProjectPromptMiddleware(), Observe()],
        output_type=Answer,
        decode=json_output(Answer),
    )
    ctx = AgentContext(project_id="p1", project_system_prompt="PROJECT RULE")
    if succeeds:
        assert (await agent.ainvoke({}, context=ctx)).answer == "ok"
    else:
        with pytest.raises(ValueError, match="after 3 attempts"):
            await agent.ainvoke({}, context=ctx)
    assert len(calls) == 3 and all(
        c[0].content.count("PROJECT RULE") == 1 for c in calls
    )
    assert all(c.project_id == "p1" for c in seen)


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_executor_backfill_keeps_interrupt_and_current_report_prompt(
    legacy,
):
    from dtest.application.runs.graph_invocation import GraphInvocation

    class State(TypedDict, total=False):
        project_system_prompt: str
        project_prompt_version: int
        approved: bool
        report: dict

    calls = []

    class Backend:
        async def ainvoke(self, messages):
            calls.append(messages)
            return AIMessage(content=ROLES[3][1])

    agent = role_builder("execution_report", chat_model(Backend()))

    async def wait_event(state):
        return {"approved": interrupt({"kind": "EXECUTOR_EVENT"})}

    async def report(state):
        result = await agent.ainvoke(
            role_payload("execution_report"),
            context=AgentContext(
                project_system_prompt=state.get("project_system_prompt", ""),
                project_prompt_version=state.get("project_prompt_version"),
            ),
        )
        return {"report": result.model_dump()}

    graph = (
        StateGraph(State)
        .add_node("wait", wait_event)
        .add_node("report", report)
        .add_edge(START, "wait")
        .add_edge("wait", "report")
        .add_edge("report", END)
        .compile(checkpointer=InMemorySaver())
    )
    config = {"configurable": {"thread_id": str(uuid4())}}
    await graph.ainvoke(
        {"project_prompt_version": 1}
        if legacy
        else {"project_system_prompt": "SAVED RULE"},
        config,
    )
    values = (await graph.aget_state(config)).values
    loader = AsyncMock(
        return_value={
            "project_system_prompt": "SAVED RULE",
            "project_prompt_version": 2,
        }
    )
    result = await GraphInvocation(
        graph, project_context_loader=loader, model_validator=None
    ).invoke(Command(resume=True), config, values=values, durability="sync")
    assert result["approved"] is True and result["report"][
        "evidence_steps"
    ] == ["profile"]
    assert calls[0][0].content.count(
        "SAVED RULE"
    ) == 1 and loader.await_count == int(legacy)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["prompt_json", "provider_json_schema"])
async def test_report_corrects_invalid_evidence_ids_inside_create_agent(mode):
    from dtest.agent_service.agents.analysis.agent_builders.execution_report.agent import (
        build_agent,
    )

    calls = []

    async def handle(request):
        calls.append(json.loads(request.content))
        ids = ["profile_data"] if len(calls) == 1 else ["profile"]
        return response(
            json.dumps({"markdown": "# Actual profile", "evidence_steps": ids})
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as ac:
        model = ChatOpenAI(
            model="test",
            api_key="test",
            base_url="http://llm.invalid/v1",
            max_retries=0,
            http_async_client=ac,
        )
        agent = build_agent(model, structured_output_mode=mode)
        result = await agent.ainvoke(
            {
                "observations": [
                    {
                        "step_id": "profile",
                        "tool_id": "profile_data",
                        "status": "SUCCEEDED",
                    }
                ]
            },
            context=AgentContext(project_system_prompt="PROJECT RULE"),
        )
    assert result.evidence_steps == ["profile"] and len(calls) == 2
    assert "Allowed IDs" in calls[1]["messages"][-1]["content"]
    assert all(
        c["messages"][0]["content"].count("PROJECT RULE") == 1 for c in calls
    )


@pytest.mark.asyncio
async def test_report_retries_model_invented_numbers_before_rendering_facts():
    from dtest.agent_service.agents.analysis.agent_builders.execution_report.agent import (
        build_agent,
    )

    calls = []

    async def handle(request):
        calls.append(json.loads(request.content))
        text = (
            "450 missing cells"
            if len(calls) == 1
            else (
                "# 해석\n\n누락된 값이 존재합니다. 실제 수치는 실행 "
                "근거를 확인하세요."
            )
        )
        return response(
            json.dumps({"markdown": text, "evidence_steps": ["profile"]})
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as ac:
        model = ChatOpenAI(
            model="test",
            api_key="test",
            base_url="http://llm.invalid/v1",
            max_retries=0,
            http_async_client=ac,
        )
        result = await build_agent(model).ainvoke(
            {
                "observations": [
                    {
                        "step_id": "profile",
                        "tool_id": "profile_data",
                        "status": "SUCCEEDED",
                    }
                ]
            }
        )
    assert len(calls) == 2 and "450" not in result.markdown
