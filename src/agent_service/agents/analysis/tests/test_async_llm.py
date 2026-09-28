"""LLM cancellation, native async transport, and mixed-node ownership checks."""
import asyncio
from contextvars import ContextVar
from threading import Event
from types import SimpleNamespace
from typing import TypedDict
from uuid import uuid4

import httpx
import langchain_openai
import pytest
from langchain.agents import create_agent
from langchain_core.messages import AIMessage
from langgraph.graph import StateGraph, START, END
from pydantic import BaseModel

from agent_config import load_agent_settings
from agent_service.agents.analysis.components.interfaces import (
    SimpleLLMAgent, StructuredLLMAgent, JsonMessageAgentAdapter, ainvoke_typed,
)
from agent_service.agents.analysis.dependencies import create_chat_model
from agent_service.agents.analysis.nodes.service_queries import make_faq_node
from agent_service.agents.analysis.testing.mock_dependencies import ScriptedAgent
from agent_service.runtime.blocking import run_sync
from app.services.run_service import RunService


class State(TypedDict, total=False):
    user_request: str
    value: str


class Answer(BaseModel):
    answer: str


def graph_for(node, after=None):
    builder = StateGraph(State).add_node('work', node).add_edge(START, 'work')
    if after is not None:
        builder.add_node('submit_executor', after).add_edge('work', 'submit_executor').add_edge('submit_executor', END)
    else:
        builder.add_edge('work', END)
    return builder.compile()


def cancel_after(monkeypatch, entered):
    async def watcher(_run_id, _stop):
        await entered.wait()
        return True
    monkeypatch.setattr(RunService, '_wait_for_cancellation', watcher)


@pytest.mark.asyncio
async def test_legacy_sync_node_can_continue_after_run_reports_cancel(monkeypatch):
    """Characterization of the still-unsafe unmanaged synchronous-node path."""
    entered = asyncio.Event()
    release, finished = Event(), Event()
    side_effects = []
    loop = asyncio.get_running_loop()
    def legacy(_state):
        loop.call_soon_threadsafe(entered.set)
        release.wait(3)
        side_effects.append('late operation')
        finished.set()
        return {'value': 'done'}
    cancel_after(monkeypatch, entered)
    try:
        with pytest.raises(RunService.CancellationRequested):
            await asyncio.wait_for(RunService._run_cancellable(uuid4(), graph_for(legacy).ainvoke({})), 2)
        assert not finished.is_set()
        assert not side_effects
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 3)
    assert side_effects == ['late operation']


@pytest.mark.asyncio
async def test_cancel_during_real_faq_node_stops_model_before_run_returns(monkeypatch):
    entered, closed = asyncio.Event(), asyncio.Event()
    submitted = []
    class Model:
        def invoke(self, *_a, **_kw):
            raise AssertionError('sync model path used')
        async def ainvoke(self, _messages):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()
    node = make_faq_node(SimpleNamespace(faq_agent=SimpleLLMAgent(Model(), 'answer')))
    async def after(_state):
        submitted.append('submitted')
        return {}
    cancel_after(monkeypatch, entered)
    with pytest.raises(RunService.CancellationRequested):
        await asyncio.wait_for(RunService._run_cancellable(uuid4(), graph_for(node, after).ainvoke({'user_request':'hello'})), 2)
    assert closed.is_set()
    assert not submitted


@pytest.mark.asyncio
async def test_two_sessions_enter_model_wait_concurrently():
    both_entered, release = asyncio.Event(), asyncio.Event()
    count = 0
    class Model:
        async def ainvoke(self, _messages):
            nonlocal count
            count += 1
            if count == 2:
                both_entered.set()
            await release.wait()
            return AIMessage(content='ok')
    agent = SimpleLLMAgent(Model(), 'answer')
    tasks = [asyncio.create_task(agent.ainvoke({'user_request':str(i)})) for i in range(2)]
    try:
        await asyncio.wait_for(both_entered.wait(), 1)
    finally:
        release.set()
        results = await asyncio.gather(*tasks)
    assert results == [{'answer':'ok'}] * 2


@pytest.mark.asyncio
async def test_mock_delay_is_cancellable_without_late_response():
    calls = []
    model = ScriptedAgent(lambda p: calls.append(p) or {'answer':'ok'}, 60000)
    task = asyncio.create_task(model.ainvoke({}))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls == []


@pytest.mark.asyncio
async def test_validation_retry_does_not_swallow_cancellation():
    calls = []
    class Model:
        async def ainvoke(self, _messages):
            calls.append(1)
            if len(calls) == 1:
                return AIMessage(content='invalid JSON')
            raise asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await StructuredLLMAgent(Model(), 'answer', Answer).ainvoke({})
    assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['plain', 'prompt_json', 'provider_json_schema', 'nested_agent', 'cancel'])
async def test_configured_provider_uses_async_http_only(monkeypatch, mode):
    calls = []
    entered, closed = asyncio.Event(), asyncio.Event()
    async def handle(request):
        calls.append(request)
        if mode == 'cancel':
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()
        return httpx.Response(200, json={'id':'test', 'object':'chat.completion', 'created':0,
            'model':'test', 'choices':[{'index':0, 'message':{'role':'assistant','content':'{"answer":"ok"}'},'finish_reason':'stop'}]})
    def no_sync(_request):
        raise AssertionError('synchronous HTTP transport used')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as async_client:
        with httpx.Client(transport=httpx.MockTransport(no_sync)) as sync_client:
            real_class = langchain_openai.ChatOpenAI
            monkeypatch.setattr(langchain_openai, 'ChatOpenAI',
                lambda **kwargs: real_class(http_async_client=async_client, http_client=sync_client, **kwargs))
            settings = load_agent_settings({'MODEL_PROVIDER':'openai_compatible', 'MODEL_NAME':'test',
                'API_BASE_URL':'http://llm.invalid/v1', 'MODEL_API_KEY':'test-key', 'MODEL_MAX_RETRIES':'0'})
            model = create_chat_model(settings)
            if mode == 'cancel':
                task = asyncio.create_task(SimpleLLMAgent(model, 'answer').ainvoke({'user_request':'test'}))
                try:
                    await asyncio.wait_for(entered.wait(), 2)
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                assert task.cancelled()
                assert closed.is_set()
            elif mode == 'plain':
                result = await SimpleLLMAgent(model, 'answer').ainvoke({'user_request':'test'})
                assert 'ok' in result['answer']
            else:
                agent = (JsonMessageAgentAdapter(create_agent(model=model, tools=[])) if mode == 'nested_agent'
                         else StructuredLLMAgent(model, 'answer', Answer, method=mode))
                assert (await ainvoke_typed(agent, {}, Answer)).answer == 'ok'
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_mixed_node_does_not_return_before_sync_operation_finishes():
    entered = asyncio.Event()
    release, finished = Event(), Event()
    submitted = []
    context = ContextVar('test_context', default='missing')
    token = context.set('run-context')
    loop = asyncio.get_running_loop()
    def operation():
        assert context.get() == 'run-context'
        loop.call_soon_threadsafe(entered.set)
        release.wait(3)
        finished.set()
    async def node(_state):
        await run_sync(operation)
        submitted.append('next side effect')
        return {}
    task = asyncio.create_task(graph_for(node).ainvoke({}))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        await asyncio.sleep(.02)
        task.cancel()
        await asyncio.sleep(.02)
        assert not task.done()
        assert not finished.is_set()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        context.reset(token)
    assert finished.is_set()
    assert task.cancelled()
    assert submitted == []
