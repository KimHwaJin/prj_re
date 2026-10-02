"""Short model references preserve exact ownership, paths, values and budgets."""
from copy import deepcopy
import json

import httpx
import pytest
from langchain_openai import ChatOpenAI

from agent_service.agents.analysis.execution.grounding import compact_evidence, grounded_message
from agent_service.agents.analysis.tests.test_answer_grounding import context, reply
from agent_service.agents.analysis.execution.report import render_evidence_markdown
from agent_service.runtime.session_analysis import encoded
from agent_service.agents.analysis.agent_builders.conversation.agent import build_agent
from agent_service.agents.analysis.planning.catalog import AssetCatalog


def short_reply(identifier='f_a', **changes):
    return reply(grounding={'scope':'analysis','source_run_id':'r','evidence_steps':['stats'],
        'facts':[],'fact_ids':[identifier]}, **changes)


def test_catalogue_resolves_exact_values_without_mutating_retained_evidence():
    c=context();evidence=c.session_analysis_context['payload'];before=deepcopy(evidence)
    view,registry=compact_evidence(evidence)
    assert view['fact_catalog']['stats']['f_a']=={'label':'mean','value':109}
    assert registry['f_a'].path==['mean']
    assert 'summary' not in view['observations'][0]
    assert '| stats.mean | 109 |' in grounded_message(short_reply(),c)
    assert evidence==before


@pytest.mark.parametrize('change',['unknown_id','foreign_run','foreign_owner','failed','incomplete','omitted','mixed','duplicate','general'])
def test_short_references_cannot_bypass_existing_grounding_rules(change):
    c=context();value=short_reply();obs=c.session_analysis_context['payload']['observations'][0]
    if change=='unknown_id':value.grounding.fact_ids=['f_missing']
    elif change=='foreign_run':value.grounding.source_run_id='other'
    elif change=='foreign_owner':c.session_analysis_context['owner']['session_id']='other'
    elif change=='failed':obs['status']='FAILED'
    elif change=='incomplete':obs['incomplete']=True
    elif change=='omitted':obs['summary_omitted']=True
    elif change=='mixed':value.grounding.facts=reply().grounding.facts
    elif change=='duplicate':value.grounding.fact_ids=['f_a','f_a']
    elif change=='general':value.grounding.scope='general';value.grounding.source_run_id=None;value.grounding.evidence_steps=[]
    with pytest.raises(ValueError):grounded_message(value,c)


def test_catalogue_keeps_typed_paths_string_keys_arrays_and_limits_distinct():
    c=context();evidence=c.session_analysis_context['payload'];obs=evidence['observations'][0]
    obs['summary']={'type':'dict','items':{'0':7,'sample':{'type':'list','items':[{'name':'<script>|`'}],'truncated':True},'shape':[10000,8],'large':'x'*900}}
    view,registry=compact_evidence(evidence)
    assert registry['f_a'].path==['0']
    assert registry['f_b'].path==['sample',0,'name']
    assert registry['f_c'].path==['shape']
    assert view['fact_catalog_limited'] and view['observations'][0]['summary_limited']
    value=short_reply();value.grounding.fact_ids=['f_a','f_b','f_c']
    text=grounded_message(value,c)
    assert '| stats.0 | 7 |' in text and '[10000, 8]' in text
    assert '&lt;script&gt;&#124;&#96;' in text and '<script>' not in text
    assert '일부 관찰이 생략되거나 제한' in text


def test_only_an_exact_server_table_suffix_is_deduplicated():
    c=context();evidence=c.session_analysis_context['payload'];observations=evidence['observations']
    narrative='## 설명\n값의 의미는 추가 검증이 필요합니다.'
    evidence['report']={'status':'generated','excerpt':narrative+'\n\n'+render_evidence_markdown(observations)}
    view,_=compact_evidence(evidence)
    assert view['report']['excerpt']==narrative and view['report']['server_evidence_table_in_catalog']
    evidence['report']['excerpt']=narrative+'\n\n## 실행 결과 근거\n사용자가 작성한 다른 설명'
    assert compact_evidence(evidence)[0]['report']['excerpt']==evidence['report']['excerpt']


def test_latest_observation_for_a_step_is_used_in_both_catalogue_and_rendering():
    c=context();evidence=c.session_analysis_context['payload']
    evidence['observations'].append({**evidence['observations'][0],'summary':{'mean':22}})
    view,_=compact_evidence(evidence)
    assert view['fact_catalog']['stats']['f_a']['value']==22
    assert '| stats.mean | 22 |' in grounded_message(short_reply(),c)
    assert '| stats.mean | 109 |' not in grounded_message(short_reply(),c)


def test_catalogue_has_bounded_size_and_discloses_omitted_facts():
    c=context();evidence=c.session_analysis_context['payload']
    evidence['observations'][0]['summary']={f'field_{i}':i for i in range(100)}
    view,registry=compact_evidence(evidence,max_chars=3000)
    assert len(encoded(view))<=3000 and view['fact_catalog_limited'] and len(registry)<512
    # Same configured budget applies when resolving IDs for publication.
    value=short_reply();assert '일부 관찰이 생략되거나 제한' in grounded_message(value,c,max_chars=3000)
    evidence['observations'][0]['summary']={f'field_{i}':i for i in range(900)}
    full,registry=compact_evidence(evidence,max_chars=1000000)
    assert len(registry)==512 and full['fact_catalog_limited']


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['prompt_json','provider_json_schema'])
async def test_production_agent_uses_short_ids_and_keeps_numeric_rejection(mode):
    calls=[]
    async def handle(request):
        body=json.loads(request.content);calls.append(body)
        evidence=next(json.loads(m['content'])['analysis'] for m in body['messages'] if m['role']=='user' and 'previous_completed_session_analysis' in m['content'])
        assert evidence['fact_catalog']['stats']['f_a']['value']==109
        value=short_reply(message='관찰값은 999입니다.' if len(calls)==1 else '실제 값은 아래 표에서 확인할 수 있습니다.')
        return httpx.Response(200,json={'id':'test','object':'chat.completion','created':0,'model':'test',
            'choices':[{'index':0,'message':{'role':'assistant','content':value.model_dump_json()},'finish_reason':'stop'}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        agent=build_agent(ChatOpenAI(model='test',api_key='test',base_url='http://llm.invalid/v1',http_async_client=client,max_retries=0),AssetCatalog(),structured_output_mode=mode)
        value=await agent.ainvoke({'request':'방금 결과 설명'},context=context())
    assert len(calls)==2 and '| stats.mean | 109 |' in grounded_message(value,context())
    assert '999' not in grounded_message(value,context())


@pytest.mark.asyncio
async def test_short_ids_publish_same_markdown_to_history_and_sse_without_executor(tmp_path,monkeypatch):
    from uuid import uuid4
    from agent_service.agents.analysis.tests.test_session_analysis_context import completed_analysis
    from agent_service.agents.analysis.agent_builders.conversation.agent import reply_schema
    from agent_service.agents.analysis.execution.grounding import completed_context
    runtime,executor,graph,config,state=await completed_analysis(tmp_path,monkeypatch)
    async def respond(value,c,datasets):
        evidence=completed_context(c,runtime.settings.agent_session_analysis_max_chars)
        _,registry=compact_evidence(evidence,max_chars=runtime.settings.agent_session_analysis_max_chars)
        key=next(k for k,reference in registry.items() if reference.step_id=='outliers' and reference.path==['outlier_indices'])
        return reply_schema(runtime.catalog,5)(kind='answer',message='이상치 후보는 아래 표에서 확인할 수 있습니다. 원인은 추가 검증이 필요합니다.',
            grounding={'scope':'analysis','source_run_id':evidence['source_run_id'],'evidence_steps':['outliers'],'fact_ids':[key]})
    monkeypatch.setattr(runtime,'respond',respond)
    following=await graph.ainvoke({**{k:state[k] for k in ('user_id','project_id','session_id','model_selection')},
        'run_id':str(uuid4()),'user_request':'방금 이상치 결과 설명'},config,durability='sync')
    message=following['final_response']['message']
    assert '| outliers.outlier_indices | [5] |' in message
    assert following['history'][-1]['content']==message
    assert following['public_events'][-1]['envelope']['data']['content'][0]['text']==message
    assert len(executor.calls)==3 and following['execution_id'] is None


@pytest.mark.asyncio
async def test_cached_agent_resolves_same_short_id_against_each_sessions_own_source():
    import asyncio
    from dataclasses import replace
    from agent_service.agents.analysis.tests.test_session_analysis_context import saved_record
    async def handle(request):
        body=json.loads(request.content)
        evidence=next(json.loads(m['content'])['analysis'] for m in body['messages'] if m['role']=='user' and 'previous_completed_session_analysis' in m['content'])
        await asyncio.sleep(.01)
        value=short_reply();value.grounding.source_run_id=evidence['source_run_id']
        return httpx.Response(200,json={'id':'test','object':'chat.completion','created':0,'model':'test',
            'choices':[{'index':0,'message':{'role':'assistant','content':value.model_dump_json()},'finish_reason':'stop'}]})
    one=context();two=replace(context(saved_record(session='second-session',value=22)),session_id='second-session')
    two.session_analysis_context['payload']['source_run_id']='second-run'
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        agent=build_agent(ChatOpenAI(model='test',api_key='test',base_url='http://llm.invalid/v1',http_async_client=client,max_retries=0),AssetCatalog())
        first,second=await asyncio.gather(*(agent.ainvoke({'request':'결과 설명'},context=c) for c in (one,two)))
    assert '| stats.mean | 109 |' in grounded_message(first,one)
    assert '| stats.mean | 22 |' in grounded_message(second,two)
    with pytest.raises(ValueError):grounded_message(first,two)
