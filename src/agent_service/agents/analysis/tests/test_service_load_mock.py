"""Current mock model boundary, native mock Executor submission and load endpoint."""
from copy import deepcopy
from dataclasses import replace
from uuid import uuid4
import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from devtools.analysis.runtime import local_runtime,local_input
from agent_service.agents.analysis.planning.runtime import PlanningRuntime
from agent_service.agents.analysis.planning.graph import build_planning_graph
from integrations.executor.client import ExecutorClient


@pytest.mark.asyncio
async def test_current_graph_reaches_review_without_constructing_llm(monkeypatch):
    import agent_service.agents.analysis.planning.runtime as module
    def forbidden(*args):raise AssertionError('LLM must not be constructed')
    monkeypatch.setattr(module,'create_chat_model',forbidden)
    runtime=local_runtime();value=local_input(runtime,'quality review')
    graph=build_planning_graph(runtime,checkpointer=InMemorySaver())
    state=await graph.ainvoke(value,{'configurable':{'thread_id':value['session_id']}})
    assert state['interaction_data']['kind']=='plan_review' and state['plan_views']
    assert not state.get('execution_id') and next(iter(runtime.agents.values())).calls==1


def test_mock_delay_rejects_invalid_values():
    for value in [-1,60001,'nan']:
        with pytest.raises(ValueError):local_runtime(delay_ms=value)


@pytest.mark.asyncio
async def test_approval_submits_current_contract_and_registers_once(tmp_path,monkeypatch):
    from scripts.loadtest import mock_executor
    monkeypatch.setattr(mock_executor,'DB_PATH',tmp_path/'submissions.sqlite')
    registered=[]
    class Bindings:
        async def register(self,**kwargs):registered.append(kwargs)
    settings=replace(local_runtime().settings,executor_submit_enabled=True,executor_source_type='INLINE',executor_base_url='http://mock-executor')
    async with ExecutorClient(settings,transport=httpx.ASGITransport(app=mock_executor.app)) as client:
        runtime=PlanningRuntime(settings,executor=client,bindings=Bindings())
        graph=build_planning_graph(runtime,checkpointer=InMemorySaver());value=local_input(runtime,'quality review')
        cfg={'configurable':{'thread_id':value['session_id']}}
        state=await graph.ainvoke(value,cfg);plan=state['plan_views'][0]
        state=await graph.ainvoke(Command(resume={'resume':{'action':'approve_plan','plan_id':plan['plan_id'],
            'plan_revision':plan['plan_revision']}}),cfg)
        assert state['__interrupt__'][0].value['kind']=='EXECUTOR_EVENT'
        assert state['execution_id']==str(registered[0]['execution_id']) and len(registered)==1
        payload=state['execution_command']
        assert all(step['payload']['source']['type']=='INLINE' for step in payload['operation']['spec']['steps'])
        retry=await client.request('POST','http://mock-executor/api/v1/executions',payload)
        assert retry['body']['execution_id']==state['execution_id']
        assert (await client.request('GET','http://mock-executor/health'))['body']['unique_submissions']==1
        changed=deepcopy(payload);changed['context']['session_id']=str(uuid4())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mock_executor.app),base_url='http://mock-executor') as raw:
            assert (await raw.post('/api/v1/executions',json=changed)).status_code==409
            assert (await raw.post('/api/v1/executions',json={})).status_code==422


def test_planning_load_scenario_and_unconnected_executor_guard():
    from scripts.loadtest.scenario import execute
    calls=[];public_id=str(uuid4())
    def request(method,path,**kwargs):
        calls.append((path,kwargs.get('json')))
        if path.endswith('/sessions'):return {'id':str(uuid4())}
        return {'run_id':public_id,'status':'waiting_input','resume_token':str(uuid4()),
            'interrupt':[{'kind':'plan_review','payload':{'plans':[]}}]}
    result=execute(request,{},'project',record=lambda *a:None)
    assert len(result['runs'])==1 and result['execution_id'] is None
    assert calls[-1][1]['input']['content'][0]['type']=='text'
    with pytest.raises(RuntimeError,match='not connected'):execute(request,{},'project',record=lambda *a:None,submit=True)
