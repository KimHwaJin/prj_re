"""Async analysis and nested LLM execution with a real disposable checkpointer."""
from typing import TypedDict
from uuid import uuid4

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command, interrupt
from sqlalchemy.engine import make_url

from tests.api_service.test_user_identity_postgres import database_url, harness
from agent_service.runtime.langgraph.checkpointer import create_checkpointer
from agent_service.factory import RoleAgent
from devtools.analysis.runtime import local_runtime, local_input
from agent_service.agents.analysis.planning.graph import build_planning_graph


def checkpoint_url(database_url):
    return make_url(database_url).set(drivername='postgresql').render_as_string(hide_password=False)


@pytest.mark.asyncio
async def test_async_analysis_resumes_approval_after_checkpoint_pool_restart(harness, database_url):
    runtime = local_runtime()
    value = local_input(runtime, 'async checkpoint test')
    config = {'configurable': {'thread_id':value['session_id']}}
    async with create_checkpointer(checkpoint_url(database_url), setup_on_start=True, min_size=1, max_size=2) as saver:
        graph = build_planning_graph(runtime, checkpointer=saver)
        state = await graph.ainvoke(value, config, durability='sync')
        assert state['interaction_data']['kind'] == 'plan_review'
        plan = state['plan_views'][0]
        calls = next(iter(runtime.agents.values())).calls
    async with create_checkpointer(checkpoint_url(database_url), setup_on_start=False, min_size=1, max_size=2) as saver:
        runtime = local_runtime()
        graph = build_planning_graph(runtime, checkpointer=saver)
        state = await graph.ainvoke(Command(resume={'resume':{'action':'approve_plan',
            'plan_id':plan['plan_id'],'plan_revision':plan['plan_revision']}}), config, durability='sync')
        assert state['approved_snapshot']['steps'] and state['final_response']['status']=='plan_approved'
        assert not runtime.agents and calls==1



@pytest.mark.asyncio
async def test_nested_async_agent_does_not_use_outer_postgres_saver(harness, database_url):
    class Model(BaseChatModel):
        @property
        def _llm_type(self):
            return 'async-only-test'
        def _generate(self, *args, **kwargs):
            raise AssertionError('synchronous model execution')
        async def _agenerate(self, *args, **kwargs):
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content='{"answer":"ok"}'))])
    class State(TypedDict, total=False):
        answer: str
        approved: bool
    inner = RoleAgent(create_agent(model=Model(), tools=[], checkpointer=False))
    async def call_model(_state):
        result = await inner.ainvoke({'question':'test'})
        return {'answer':result['messages'][-1].content}
    async def approve(_state):
        return {'approved':interrupt({'kind':'approval'})}
    async with create_checkpointer(checkpoint_url(database_url), setup_on_start=True, min_size=1, max_size=2) as saver:
        graph = (StateGraph(State).add_node('model',call_model).add_node('approval',approve)
            .add_edge(START,'model').add_edge('model','approval').add_edge('approval',END).compile(checkpointer=saver))
        config={'configurable':{'thread_id':str(uuid4())}}
        state=await graph.ainvoke({},config)
        assert state['__interrupt__']
        result=await graph.ainvoke(Command(resume=True),config)
        assert result == {'answer':'{"answer":"ok"}', 'approved':True}
