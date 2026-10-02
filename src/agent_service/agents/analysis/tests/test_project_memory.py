"""Real create_agent policies with local model transport; no external model or DB."""
import asyncio
import copy
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from agent_service.context import AgentContext
from agent_service.agents.analysis.agent_builders.conversation.agent import build_agent, reply_schema
from agent_service.agents.analysis.planning.catalog import AssetCatalog
from agent_service.agents.analysis.tests.test_conversation_performance import model,response
from agent_service.agents.analysis.planning.runtime import PlanningRuntime
from agent_service.agents.analysis.planning.graph import build_planning_graph
from agent_service.runtime.project_memory import validate_memory_proposals
from service_contracts.project_memory import MemoryConflict
from service_settings import load_settings

QUOTE='보고서는 원인과 다음 행동 중심으로 간결하게 작성해줘'

def snapshot(project='p',user='u',entries=None):
    return {'schema_version':1,'user_id':user,'project_id':project,'entries':entries or []}

def proposal(**kw):
    return {'section':'report_preferences','key':'style','content':QUOTE,'quote':QUOTE,'intent':'preference_change','expected_version':0,**kw}

def value(updates=None):
    return {'kind':'answer','message':'요청한 방향을 참고하겠습니다.','plans':[],
        'grounding':{'scope':'general'},'memory_updates':updates or []}

def provider(record=None,conflict=False):
    async def read(store):
        assert isinstance(store, InMemoryStore)
        return copy.deepcopy(record or snapshot())
    return SimpleNamespace(read=AsyncMock(side_effect=read),apply=AsyncMock(side_effect=MemoryConflict('changed') if conflict else None,
                                      return_value={'status':'saved','entries':[{'section':'report_preferences','key':'style','version':1}]}))

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['prompt_json','provider_json_schema'])
async def test_one_read_retries_no_durable_write_before_validation_and_no_extra_model(mode):
    memory=provider();calls=[]
    async def handle(request):
        calls.append(json.loads(request.content))
        # First extraction is fabricated and must never be written.
        updates=[proposal(content='가짜 선호',quote='가짜 선호')] if len(calls)==1 else [proposal()]
        return response({'role':'assistant','content':json.dumps(value(updates),ensure_ascii=False)})
    ctx=AgentContext(user_id='u',project_id='p',session_id='s',project_memory_policy=memory,project_memory_auto_write=True)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result=await build_agent(model(client),AssetCatalog(),structured_output_mode=mode,store=InMemoryStore()).ainvoke({'request':QUOTE},context=ctx)
    assert len(calls)==2 and memory.read.await_count==1 and memory.apply.await_count==1
    assert result._memory_result['status']=='saved'
    assert memory.apply.call_args.args[1][0]['quote']==QUOTE
    assert all(sum('"reference_type": "project_memory"' in str(m['content']) for m in body['messages'])==1 for body in calls)
    assert all('Workflow definition JSON Schema' not in body['messages'][0]['content'] for body in calls)

@pytest.mark.parametrize('change,mode,entry',[
    (proposal(section='shared_findings'),True,None),
    (proposal(content='결과는 109',quote='결과는 109'),True,None),
    (proposal(content='/workspace/pv/private.parquet',quote='/workspace/pv/private.parquet'),True,None),
    (proposal(),False,None),
    (proposal(expected_version=2),True,None),
    (proposal(),True,{'section':'report_preferences','key':'style','content':'','version':0,'is_deleted':True}),
    (proposal(expected_version=1),True,{'section':'report_preferences','key':'style','content':QUOTE,'version':1,'is_deleted':False}),
])
def test_invalid_sharing_proposals_are_rejected(change,mode,entry):
    ctx=AgentContext(user_id='u',project_id='p',project_memory_policy=provider(),project_memory_auto_write=mode)
    from langchain_core.messages import HumanMessage
    request=SimpleNamespace(runtime=SimpleNamespace(context=ctx),state={'project_memory_snapshot':snapshot(entries=[entry] if entry else [])},
                            messages=[HumanMessage(content=json.dumps({'request':QUOTE+' 결과는 109 /workspace/pv/private.parquet'}))])
    result=reply_schema(AssetCatalog(),5)(**value([change]))
    with pytest.raises(ValueError):validate_memory_proposals(result,request)

@pytest.mark.asyncio
async def test_cached_agent_concurrent_projects_and_next_invocation_fresh_read():
    one=provider(snapshot('p1',entries=[{'section':'background','key':'purpose','content':'one','version':1,'is_deleted':False}]))
    two=provider(snapshot('p2',entries=[{'section':'background','key':'purpose','content':'two','version':1,'is_deleted':False}]))
    seen=[]
    async def handle(request):
        body=json.loads(request.content)
        current=json.loads(next(m['content'] for m in body['messages'] if m['role']=='user'))['request']
        memory=next(json.loads(m['content'])['memory'] for m in body['messages'] if '"reference_type": "project_memory"' in str(m['content']))
        seen.append((current,memory['project_id'],memory['entries'][0]['content']))
        await asyncio.sleep(.01)
        return response({'role':'assistant','content':json.dumps(value())})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        agent=build_agent(model(client),AssetCatalog(),store=InMemoryStore())
        await asyncio.gather(*(agent.ainvoke({'request':p},context=AgentContext(user_id='u',project_id=p,project_memory_policy=store)) for p,store in [('p1',one),('p2',two)]))
        one.read.side_effect=lambda store:snapshot('p1',entries=[{'section':'background','key':'purpose','content':'fresh','version':2,'is_deleted':False}])
        await agent.ainvoke({'request':'next'},context=AgentContext(user_id='u',project_id='p1',project_memory_policy=one))
    assert set(seen)=={('p1','p1','one'),('p2','p2','two'),('next','p1','fresh')}
    assert one.read.await_count==2 and two.read.await_count==1
    assert one.apply.await_count==two.apply.await_count==0

@pytest.mark.asyncio
async def test_wrong_owner_snapshot_and_conflict_do_not_publish_success():
    wrong=provider(snapshot(user='other'));calls=[]
    async def handle(request):
        calls.append(request)
        return response({'role':'assistant','content':json.dumps(value([proposal()]))})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        agent=build_agent(model(client),AssetCatalog(),store=InMemoryStore())
        with pytest.raises(ValueError,match='owner'):
            await agent.ainvoke({'request':QUOTE},context=AgentContext(user_id='u',project_id='p',project_memory_policy=wrong))
        memory=provider(conflict=True)
        reply=await agent.ainvoke({'request':QUOTE},context=AgentContext(user_id='u',project_id='p',project_memory_policy=memory,project_memory_auto_write=True))
    assert len(calls)==1 and reply._memory_result['status']=='not_saved'

@pytest.mark.asyncio
async def test_graph_publishes_actual_memory_outcome_and_preserves_user_message():
    settings=load_settings(config={'MODEL_PROVIDER':'mock'},environ={}).agent
    runtime=PlanningRuntime(settings)
    async def respond(state,*args):
        reply=reply_schema(runtime.catalog,5)(**value())
        reply._memory_result={'status':'not_saved','entries':[],'reason':'changed'}
        return reply
    runtime.respond=respond
    graph=build_planning_graph(runtime,checkpointer=InMemorySaver())
    from uuid import uuid4
    state=await graph.ainvoke({'user_id':'u','project_id':'p','session_id':'s','run_id':str(uuid4()),'user_request':QUOTE,
                             'model_selection':runtime.models.select().model_dump()}, {'configurable':{'thread_id':'s'}})
    assert state['history'][-1]['content']==state['final_response']['message']
    assert state['final_response']['project_memory']['status']=='not_saved'
    events=[e['envelope'] for e in state['public_events'] if e['envelope']['data'].get('kind')=='project_memory']
    assert [e['type'] for e in events]==['activity.started','activity.completed']
    assert events[0]['data']['activity_id']==events[1]['data']['activity_id']
    assert '저장하지 않았습니다' in events[1]['data']['title']
    assert state['execution_id'] is None

@pytest.mark.parametrize('mode',['off','manual','auto_context'])
def test_settings_priority_and_runtime_binding(mode):
    from unittest.mock import Mock
    configured=load_settings(config={'MODEL_PROVIDER':'mock','AGENT_PROJECT_MEMORY_MODE':mode},environ={'AGENT_PROJECT_MEMORY_MODE':'off'})
    assert configured.agent.agent_project_memory_mode==mode
    factory=Mock(return_value=provider())
    runtime=PlanningRuntime(configured.agent,memory_policy_factory=factory)
    context=runtime.bind_context({'user_id':'u','project_id':'p','session_id':'s','run_id':'r'},AgentContext(user_id='u',project_id='p'))
    assert (context.project_memory_policy is not None)==(mode!='off')
    assert context.project_memory_auto_write==(mode=='auto_context')
    assert factory.call_count==(0 if mode=='off' else 1)


@pytest.mark.asyncio
async def test_native_store_namespaces_are_read_and_identity_checked():
    from service_contracts.memory_store import read_memory, memory_namespace
    store=InMemoryStore()
    entry={'section':'background','key':'purpose','content':'품질 분석','version':1,'is_deleted':False}
    await store.aput((*memory_namespace('u','p'),'background'),'purpose',entry)
    await store.aput((*memory_namespace('other','p'),'background'),'purpose',{**entry,'content':'다른 사용자'})
    assert (await read_memory(store,'u','p'))['entries']==[entry]
    await store.aput((*memory_namespace('u','p'),'background'),'purpose',{**entry,'key':'wrong'})
    with pytest.raises(ValueError,match='identity'):await read_memory(store,'u','p')
