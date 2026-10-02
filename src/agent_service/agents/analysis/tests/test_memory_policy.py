"""Memory budgets/scope policies at real middleware and native Store boundaries."""
import json
from dataclasses import replace
from types import SimpleNamespace
import httpx
import pytest
from langgraph.store.memory import InMemoryStore
from langchain_core.messages import HumanMessage
from service_settings import load_settings, ConfigurationError
from service_contracts.project_memory import MemoryLimits
from agent_service.context import AgentContext
from agent_service.runtime.memory_selection import select_memory, dumps, token_estimate
from agent_service.runtime.project_memory import validate_memory_proposals
from agent_service.agents.analysis.agent_builders.conversation.agent import build_agent, reply_schema
from agent_service.agents.analysis.planning.catalog import AssetCatalog
from agent_service.agents.analysis.tests.test_project_memory import snapshot, provider, proposal, value, QUOTE
from agent_service.agents.analysis.tests.test_conversation_performance import model,response

def entry(key, content, section='report_preferences', **kwargs):
    return {'section':section,'key':key,'content':content,'version':1,'is_deleted':False,**kwargs}

@pytest.mark.parametrize('field,value', [('max_topics',0),('max_updates',33),('topic_max_chars',0),
    ('max_chars',1023),('prompt_max_tokens',-1),('prompt_max_chars',-1)])
def test_invalid_limits_do_not_fall_back(field,value):
    with pytest.raises(ConfigurationError):
        load_settings(config={'AGENT_PROJECT_MEMORY_'+field.upper():value},environ={})

def test_config_wins_and_limits_can_be_raised_or_disabled():
    s=load_settings(config={'service':{'agent':{'agent_project_memory_max_topics':100,'agent_project_memory_max_updates':6,
        'agent_project_memory_topic_max_chars':2000,'agent_project_memory_prompt_max_tokens':0}}},
        environ={'AGENT_PROJECT_MEMORY_MAX_TOPICS':'1'})
    limits=MemoryLimits.from_settings(s.agent)
    assert (limits.max_topics,limits.max_updates,limits.topic_max_chars,limits.prompt_max_tokens)==(100,6,2000,0)
    with pytest.raises(ConfigurationError):load_settings(config={'AGENT_PROJECT_MEMORY_MAX_TOPICS':2,'AGENT_PROJECT_MEMORY_MAX_UPDATES':3},environ={})

def test_role_filters_provenance_and_deleted_markers_never_enter_model_input():
    records=[entry('style','쉬운 보고서'),entry('method','이상치부터 확인','analysis_preferences'),
        entry('goal','원인 분석','background'),entry('deleted','삭제 전 본문',is_deleted=True),
        entry('notes','사용자 공유 메모','shared_findings',source={'quote':'검증되지 않은 인용','run_id':'private-source'})]
    document=snapshot(entries=records)
    report=select_memory(document,role='analysis_execution_report',request='',limits=MemoryLimits())
    repair=select_memory(document,role='analysis_execution_repair',request='',limits=MemoryLimits())
    assert {e['section'] for e in report['memory']['entries']}=={'report_preferences','background','shared_findings'}
    assert {e['section'] for e in repair['memory']['entries']}=={'background','analysis_preferences'}
    assert all(e['key']!='deleted' for e in report['memory']['entries'])
    assert 'private-source' not in dumps(report) and 'source' not in report['memory']['entries'][-1]
    assert len(document['entries'])==5 # selection does not mutate or delete Store data

def test_complete_message_budget_and_relevance_before_alphabetical_order():
    limits=MemoryLimits(prompt_max_chars=2000,prompt_max_tokens=1900)
    doc=snapshot(entries=[entry('aaa','x'*1000),entry('zzz','이상치는 제거 전에 확인한다','analysis_preferences'),
                          entry('bbb','x'*1000)])
    result=select_memory(doc,role='analysis_conversation',request='이상치 확인 방법',limits=limits)
    assert result['memory']['entries'][0]['key']=='zzz'
    assert len(dumps(result))<=limits.prompt_max_chars and token_estimate(dumps(result))<=limits.prompt_max_tokens
    assert result['selection']['omitted_topics']==2
    assert select_memory(doc,role='analysis_conversation',request='',limits=replace(limits,prompt_max_tokens=10)) is None
    korean=snapshot(entries=[entry('style','보고서는 쉽게 작성한다')])
    result=select_memory(korean,role='analysis_conversation',request='',limits=MemoryLimits())
    assert token_estimate(dumps(result))>len(dumps(result))

@pytest.mark.asyncio
async def test_zero_prompt_budget_does_not_read_or_auto_write():
    memory=provider();seen=[]
    async def handle(request):
        body=json.loads(request.content);seen.append(body)
        return response({'role':'assistant','content':json.dumps(value())})
    ctx=AgentContext(user_id='u',project_id='p',project_memory_policy=memory,project_memory_auto_write=True,
                     project_memory_limits=MemoryLimits(prompt_max_tokens=0))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        await build_agent(model(client),AssetCatalog(),store=InMemoryStore()).ainvoke({'request':QUOTE},context=ctx)
    assert memory.read.await_count==memory.apply.await_count==0
    assert all('"reference_type": "project_memory"' not in m['content'] for m in seen[0]['messages'])

@pytest.mark.parametrize('current,quote,content,valid',[
    ('앞으로 보고서는 비전문가 대상으로 쉽게 작성해줘','앞으로 보고서는 비전문가 대상으로 쉽게 작성해줘','보고서는 비전문가를 대상으로 쉽게 작성한다',True),
    ('이번 보고서는 짧게 작성해줘','짧게 작성해줘','보고서는 짧게 작성한다',False),
    ('이번 분석에서는 이상치를 빼줘','이번 분석에서는 이상치를 빼줘','이상치는 제거한다',False),
    ('데이터를 확인해줘','데이터를 확인해줘','데이터 확인을 선호한다',False),
    ('앞으로 보고서는 쉽게 써줘','이전 대화에서 한 말','보고서는 쉽게 작성한다',False),
    ('앞으로 보고서는 평균 109를 기억해줘','앞으로 보고서는 평균 109를 기억해줘','평균을 기억한다',False),
    ('이번 보고서는 짧게. 앞으로 보고서는 원인 중심으로 써줘','앞으로 보고서는 원인 중심으로 써줘','보고서는 원인 중심으로 작성한다',True),
])
def test_current_quote_supported_normalization_and_session_only_rejection(current,quote,content,valid):
    ctx=AgentContext(user_id='u',project_id='p',project_memory_policy=provider(),project_memory_auto_write=True)
    request=SimpleNamespace(runtime=SimpleNamespace(context=ctx),state={'project_memory_snapshot':snapshot()},
        messages=[HumanMessage(content=json.dumps({'request':current}))])
    reply=reply_schema(AssetCatalog(),5)(**value([proposal(content=content,quote=quote)]))
    if valid:validate_memory_proposals(reply,request)
    else:
        with pytest.raises(ValueError):validate_memory_proposals(reply,request)

def test_configured_output_schema_batch_and_content_limits():
    limits=MemoryLimits(max_updates=6,topic_max_chars=2000)
    schema=reply_schema(AssetCatalog(),5,memory_limits=limits)
    reply=schema(**value([proposal(key='topic_'+chr(97+i),content='x'*1500) for i in range(6)]))
    assert len(reply.memory_updates)==6
    with pytest.raises(ValueError):schema(**value([proposal(key='topic_'+chr(97+i)) for i in range(7)]))
    with pytest.raises(ValueError):reply_schema(AssetCatalog(),5,memory_limits=MemoryLimits(topic_max_chars=10))(**value([proposal()]))

def test_shipped_yaml_with_commented_agent_examples_loads_default_limits():
    from pathlib import Path
    root=Path(__file__).resolve().parents[5]
    settings=load_settings(environ={},root=root)
    assert MemoryLimits.from_settings(settings.agent)==MemoryLimits()
