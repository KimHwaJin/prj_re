"""Exercise real create_agent graphs with async-only in-process model transport."""
import asyncio
import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt
from typing import TypedDict

from agent_service.context import AgentContext
from agent_service.factory import build_role_agent, json_output
from agent_service.middleware import ProjectPromptMiddleware
from agent_service.agents.analysis.agent_builders import faq, skill_selector
from agent_service.agents.analysis.dependencies import create_llm_dependencies
from agent_service.agents.analysis.graph import build_analysis_workflow_graph
from agent_service.agents.analysis.testing.mock_dependencies import workflow_plan
from agent_service.agents.analysis.schemas.agents.workflow_generator_schema import SkillSelectionOutput
from agent_service.agents.analysis.tests.model_helpers import chat_model
from agent_service.agents.analysis.tests.test_service_load_mock import mock_settings, action


def response(content):
    return httpx.Response(200, json={"id": "test", "object": "chat.completion", "created": 0,
        "model": "test", "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}]})


ROLES = [
    ("routing", "analysis"),
    ("intent_classifier", "failure_prediction"),
    ("faq", "FAQ answer"),
    ("report_writer", "# Report"),
    ("skill_selector", '{"skill_names":["data_quality_check"]}'),
    ("conditional_decider", '{"decisions":[{"tool_id":"tool-1","decision":"include","reason":"evidence"}]}'),
    ("workflow_generator", json.dumps(workflow_plan({"user_request": "profile"}))),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("role,content", ROLES)
@pytest.mark.parametrize("mode", ["prompt_json", "provider_json_schema"])
async def test_every_role_uses_async_agent_and_project_prompt(role, content, mode):
    calls = []
    async def handle(request):
        calls.append(json.loads(request.content))
        return response(content)
    def no_sync(request):
        raise AssertionError("sync HTTP called")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as ac:
        with httpx.Client(transport=httpx.MockTransport(no_sync)) as sc:
            model = ChatOpenAI(model="test", api_key="test", base_url="http://llm.invalid/v1", max_retries=0,
                http_async_client=ac, http_client=sc)
            module = importlib.import_module(f"agent_service.agents.analysis.agent_builders.{role}")
            kwargs = {"structured_output_mode": mode} if role in {"skill_selector", "conditional_decider", "workflow_generator"} else {}
            agent = module.build_agent(model, **kwargs)
            result = await agent.ainvoke({"user_request": "hello"}, context=AgentContext(
                user_id="private-user-id", project_id="private-project-id", session_id="private-session-id",
                project_system_prompt="PROJECT RULE", project_prompt_version=4))
    assert result is not None
    assert agent.agent.checkpointer is False
    assert len(calls) == 1
    messages = calls[0]["messages"]
    assert messages[0]["content"].count("PROJECT RULE") == 1
    assert "private-project-id" not in json.dumps(messages)
    if kwargs and mode == "provider_json_schema":
        assert calls[0]["response_format"]["type"] == "json_schema"
    else:
        assert "response_format" not in calls[0]


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
            return AIMessage(content="answer")
    agent = faq.build_agent(chat_model(Backend()))
    await asyncio.gather(*(agent.ainvoke({"user_request": name}, context=AgentContext(project_system_prompt=name))
        for name in ["PROJECT_A", "PROJECT_B"]))
    for messages in calls:
        own = messages[-1].content
        other = "PROJECT_B" if own == "PROJECT_A" else "PROJECT_A"
        assert messages[0].content.count(own) == 1
        assert other not in messages[0].content


@pytest.mark.asyncio
@pytest.mark.parametrize("succeeds", [True, False])
async def test_json_retry_is_bounded_and_reapplies_prompt_without_duplication(succeeds):
    calls = []
    seen_contexts = []
    class Backend:
        async def ainvoke(self, messages):
            calls.append(messages)
            return AIMessage(content='{"skill_names":["data_quality_check"]}' if succeeds and len(calls) == 3 else "invalid")
    class Observe(AgentMiddleware):
        async def awrap_model_call(self, request, handler):
            seen_contexts.append(request.runtime.context)
            return await handler(request)
    agent = build_role_agent(chat_model(Backend()), name="retry", system_prompt="role", tools=[],
        middleware=[ProjectPromptMiddleware(), Observe()], output_type=SkillSelectionOutput,
        decode=json_output(SkillSelectionOutput))
    ctx = AgentContext(project_id="p1", project_system_prompt="PROJECT RULE")
    if succeeds:
        assert (await agent.ainvoke({}, context=ctx)).skill_names == ["data_quality_check"]
    else:
        with pytest.raises(ValueError, match="after 3 attempts"):
            await agent.ainvoke({}, context=ctx)
    assert len(calls) == 3
    assert all(c[0].content.count("PROJECT RULE") == 1 for c in calls)
    assert all(c.project_id == "p1" for c in seen_contexts)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["prompt_json", "provider_json_schema"])
async def test_real_role_agents_keep_project_snapshot_across_hitl_and_rebuild(monkeypatch, mode):
    from agent_service.agents.analysis import dependencies
    calls = []
    plan_calls = []
    async def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        prompt = body["messages"][0]["content"]
        if "Classify the request and return exactly one label" in prompt:
            payload = json.loads(body["messages"][-1]["content"])
            return response("failure_prediction" if "enabled_analysis_intents" in payload else "analysis")
        if '"title": "SkillSelectionOutput"' in prompt:
            return response('{"skill_names":["data_quality_check"]}')
        plan_calls.append(body)
        return response("{}" if len(plan_calls) == 1 else json.dumps(workflow_plan({"user_request": "profile"})))
    settings = mock_settings(MODEL_PROVIDER="openai_compatible", MODEL_NAME="test", MODEL_API_KEY="test", API_BASE_URL="http://llm.invalid/v1", MODEL_STRUCTURED_OUTPUT_MODE=mode)
    saver = InMemorySaver()
    config = {"configurable": {"thread_id": str(uuid4())}}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as ac:
        model = ChatOpenAI(model="test", api_key="test", base_url="http://llm.invalid/v1", http_async_client=ac, max_retries=0)
        monkeypatch.setattr(dependencies, "create_chat_model", lambda settings: model)
        graph = build_analysis_workflow_graph(create_llm_dependencies(settings), settings, checkpointer=saver)
        state = await graph.ainvoke({"user_request": "profile", "user_id": str(uuid4()), "project_id": str(uuid4()),
            "session_id": config["configurable"]["thread_id"], "project_system_prompt": "SAVED PROJECT RULE", "project_prompt_version": 7}, config)
        assert action(state) == "data_selection"
        # A new graph instance must reconstruct the context from the outer checkpoint.
        graph = build_analysis_workflow_graph(create_llm_dependencies(settings), settings, checkpointer=saver)
        for answer in ["mock", {"objective": "EDA"}, {"candidate_number": 1}]:
            state = await graph.ainvoke(Command(resume=answer), config)
        assert action(state) == "workflow_approval"
    from agent_config import ENABLED_ANALYSIS_INTENTS
    assert len(calls) == 4 + (len(ENABLED_ANALYSIS_INTENTS) > 1)
    assert len(plan_calls) == 2
    assert all(c["messages"][0]["content"].count("SAVED PROJECT RULE") == 1 for c in calls)
    assert state["project_prompt_version"] == 7
    # Role agents use the public checkpointer=False setting, leaving no inner checkpoints.
    assert all(not c.config["configurable"].get("checkpoint_ns") for c in saver.list(config))


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_executor_backfill_keeps_interrupt_and_report_prompt(legacy):
    from app.agent_worker.langgraph_adapter import LangGraphEventAdapter
    from agent_service.agents.analysis.context import context_from_state
    from agent_service.agents.analysis.agent_builders import report_writer
    class State(TypedDict, total=False):
        project_system_prompt: str
        project_prompt_version: int
        approved: bool
        report: dict
    calls = []
    class Backend:
        async def ainvoke(self, messages):
            calls.append(messages)
            return AIMessage(content="# Done")
    agent = report_writer.build_agent(chat_model(Backend()))
    async def wait_event(state):
        return {"approved": interrupt({"kind": "EXECUTOR_EVENT"})}
    async def report(state):
        return {"report": await agent.ainvoke({}, context=context_from_state(state))}
    graph = (StateGraph(State).add_node("wait", wait_event).add_node("report", report)
        .add_edge(START, "wait").add_edge("wait", "report").add_edge("report", END)
        .compile(checkpointer=InMemorySaver()))
    config = {"configurable": {"thread_id": str(uuid4())}}
    initial = {"project_prompt_version": 1} if legacy else {"project_system_prompt": "SAVED RULE"}
    await graph.ainvoke(initial, config)
    values = (await graph.aget_state(config)).values
    loader = AsyncMock(return_value={"project_system_prompt": "SAVED RULE", "project_prompt_version": 2})
    adapter = LangGraphEventAdapter(graph, project_context_loader=loader)
    result = await adapter._invoke(Command(resume=True), config, values=values, durability="sync")
    assert result["approved"] is True
    assert result["report"] == {"content": "# Done"}
    assert calls[0][0].content.count("SAVED RULE") == 1
    assert loader.await_count == int(legacy)
