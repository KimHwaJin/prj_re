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

from app.test.test_user_identity_postgres import database_url, harness
from agent_service.runtime.langgraph.checkpointer import create_checkpointer
from agent_service.factory import RoleAgent
from agent_service.agents.analysis.dependencies import create_llm_dependencies
from agent_service.agents.analysis.graph import build_analysis_workflow_graph
from agent_service.agents.analysis.tests.test_service_load_mock import mock_settings, action


def checkpoint_url(database_url):
    return make_url(database_url).set(drivername='postgresql').render_as_string(hide_password=False)


@pytest.mark.asyncio
async def test_async_analysis_resumes_approval_after_checkpoint_pool_restart(harness, database_url):
    settings = mock_settings(EXECUTOR_SOURCE_TYPE='INLINE')
    session = str(uuid4())
    config = {'configurable': {'thread_id':session}}
    async with create_checkpointer(checkpoint_url(database_url), setup_on_start=True, min_size=1, max_size=2) as saver:
        graph = build_analysis_workflow_graph(create_llm_dependencies(settings), settings, checkpointer=saver)
        state = await graph.ainvoke({'user_request':'async checkpoint test','session_id':session,
            'user_id':str(uuid4()),'project_id':str(uuid4())}, config)
        for response in ['mock', {'objective':'EDA'}, {'candidate_number':1}]:
            state = await graph.ainvoke(Command(resume=response), config)
        assert action(state) == 'workflow_approval'
    async with create_checkpointer(checkpoint_url(database_url), setup_on_start=False, min_size=1, max_size=2) as saver:
        graph = build_analysis_workflow_graph(create_llm_dependencies(settings), settings, checkpointer=saver)
        state = await graph.ainvoke(Command(resume={'approved':True}), config)
        assert state['execution_steps']
        assert state['executor_submit_response']['skipped']


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
