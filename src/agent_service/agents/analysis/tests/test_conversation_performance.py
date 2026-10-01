"""Model request boundaries: lazy planning, exact evidence, retries and cached isolation."""
import asyncio
import json
from importlib.resources import files

import httpx
import pytest
from langchain_openai import ChatOpenAI

from agent_service.agents.analysis.agent_builders.conversation.agent import build_agent
from agent_service.agents.analysis.planning.catalog import AssetCatalog
from agent_service.context import AgentContext
from agent_service.agents.analysis.tests.test_answer_grounding import context, reply


def response(message, reason='stop'):
    return httpx.Response(200, json={'id':'test', 'object':'chat.completion', 'created':0, 'model':'test',
        'choices':[{'index':0, 'message':message, 'finish_reason':reason}]})


def skill_call(identifier):
    return {'role':'assistant', 'content':'', 'tool_calls':[{'id':identifier, 'type':'function',
        'function':{'name':'read_skill', 'arguments':'{"skill_id":"data_quality_check"}'}}]}


def selection():
    return {'kind':'planning','message':'새 분석 계획을 준비합니다.','plans':[],
        'skill_ids':['data_quality_check'],'grounding':None}


def proposal():
    document=json.loads(files('agent_service.agents.analysis.planning').joinpath('fixtures/quality-review.json').read_text())
    return {'kind':'plans','message':'등록된 도구로 분석 계획을 준비했습니다.','grounding':None,
        'plans':[{'definition':document,'input_values':{'dataset':'default-nce'}}]}


def model(client):
    return ChatOpenAI(model='test',api_key='test',base_url='http://llm.invalid/v1',
        http_async_client=client,max_retries=0)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['prompt_json','provider_json_schema'])
async def test_answer_has_evidence_and_no_full_planning_contract(mode):
    calls=[]
    async def handle(request):
        body=json.loads(request.content);calls.append(body)
        return response({'role':'assistant','content':json.dumps(reply().model_dump(),ensure_ascii=False)})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result=await build_agent(model(client),AssetCatalog(),structured_output_mode=mode).ainvoke(
            {'request':'방금 결과를 설명해줘','history':[{'role':'assistant','tool_calls':[{'name':'read_skill'}]}]},context=context())
    assert result.kind=='answer' and len(calls)==1
    assert not calls[0].get('tools')
    system=calls[0]['messages'][0]['content']
    assert 'Workflow definition JSON Schema' not in system
    assert 'Planning contract (metadata has been requested)' not in system
    assert 'previous_completed_session_analysis' in str(calls[0]['messages'])
    assert result.grounding.facts[0].path==['mean']


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['prompt_json','provider_json_schema'])
async def test_new_calculation_loads_contract_after_skill_lookup_and_preserves_plan_validation(mode):
    calls=[]
    async def handle(request):
        body=json.loads(request.content);calls.append(body)
        if len(calls)==1:return response({'role':'assistant','content':json.dumps(selection())})
        return response({'role':'assistant','content':json.dumps(proposal(),ensure_ascii=False)})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result=await build_agent(model(client),AssetCatalog(),structured_output_mode=mode).ainvoke(
            {'request':'이 데이터로 새 분석을 실행해줘'},context=context())
    assert result.kind=='plans' and len(calls)==2
    assert not calls[0].get('tools') and calls[1].get('tools')
    assert 'Workflow definition JSON Schema' not in calls[0]['messages'][0]['content']
    assert calls[1]['messages'][0]['content'].count('Workflow definition JSON Schema')==1
    assert any(m['role']=='tool' and 'data_load' in m['content'] for m in calls[1]['messages'])
    assert result.plans[0].definition['schema_version']=='2.0-draft'


@pytest.mark.asyncio
async def test_missing_discovery_is_corrected_before_publishing_plans():
    calls=[]
    async def handle(request):
        body=json.loads(request.content);calls.append(body)
        if len(calls)==2:return response({'role':'assistant','content':json.dumps(selection())})
        return response({'role':'assistant','content':json.dumps(proposal(),ensure_ascii=False)})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result=await build_agent(model(client),AssetCatalog()).ainvoke({'request':'분석해줘'},context=AgentContext())
    assert result.kind=='plans' and len(calls)==3
    assert 'discovery first' in str(calls[1]['messages'])
    assert 'Workflow definition JSON Schema' not in calls[1]['messages'][0]['content']
    assert 'Workflow definition JSON Schema' in calls[2]['messages'][0]['content']


@pytest.mark.asyncio
async def test_cached_agent_does_not_leak_planning_contract_across_concurrent_sessions():
    calls=[];seen=set();ready=asyncio.Event()
    async def handle(request):
        body=json.loads(request.content);calls.append(body)
        payload=json.loads(next(m['content'] for m in body['messages'] if m['role']=='user'))
        name=payload['request'];seen.add(name)
        if len(seen)==2:ready.set()
        await asyncio.wait_for(ready.wait(),3)
        if name=='plan' and not any(m['role']=='tool' for m in body['messages']):
            return response({'role':'assistant','content':json.dumps(selection())})
        value=proposal() if name=='plan' else {'kind':'answer','message':'일반 답변입니다.','plans':[]}
        return response({'role':'assistant','content':json.dumps(value,ensure_ascii=False)})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        agent=build_agent(model(client),AssetCatalog())
        results=await asyncio.gather(*(agent.ainvoke({'request':name},context=AgentContext(session_id=name)) for name in ('plan','answer')))
        second=await agent.ainvoke({'request':'answer'},context=AgentContext(session_id='later'))
    assert [r.kind for r in results]==['plans','answer'] and second.kind=='answer'
    for body in calls:
        payload=json.loads(next(m['content'] for m in body['messages'] if m['role']=='user'))
        if payload['request']=='answer':assert 'Workflow definition JSON Schema' not in body['messages'][0]['content']


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['prompt_json','provider_json_schema'])
async def test_invalid_selection_never_executes_unknown_skills(mode):
    calls=[]
    async def handle(request):
        body=json.loads(request.content);calls.append(body)
        if len(calls)==1:
            value={**selection(),'skill_ids':['unknown']}
        else:value={'kind':'answer','message':'사용 가능한 도구가 없어 추가 확인이 필요합니다.','plans':[]}
        return response({'role':'assistant','content':json.dumps(value,ensure_ascii=False)})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        agent=build_agent(model(client),AssetCatalog(),structured_output_mode=mode)
        if mode=='provider_json_schema':
            # ProviderStrategy parses Pydantic before middleware receives a result;
            # preserve its existing fail-fast contract for invalid model output.
            with pytest.raises(ValueError,match='registered skill_ids'):
                await agent.ainvoke({'request':'분석해줘'},context=AgentContext())
        else:
            result=await agent.ainvoke({'request':'분석해줘'},context=AgentContext())
            assert result.kind=='answer' and len(calls)==2
    assert all(not call.get('tools') for call in calls)
    assert not any(m['role']=='tool' for call in calls for m in call['messages'])


@pytest.mark.asyncio
async def test_unadvertised_initial_tool_call_cannot_bypass_selection_boundary():
    calls=[]
    async def handle(request):
        body=json.loads(request.content);calls.append(body)
        if len(calls)==1:return response(skill_call('illegal'),'tool_calls')
        return response({'role':'assistant','content':json.dumps({'kind':'answer','message':'일반 설명입니다.','plans':[]})})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result=await build_agent(model(client),AssetCatalog()).ainvoke({'request':'설명해줘'},context=AgentContext())
    assert result.kind=='answer' and len(calls)==2
    assert not any(m['role']=='tool' or m.get('tool_calls') for call in calls[1:] for m in call['messages'])
