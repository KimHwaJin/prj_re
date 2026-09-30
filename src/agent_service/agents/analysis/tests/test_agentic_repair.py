"""Exercise code, model policy and durable resumes with an explicit Executor double."""
import hashlib
import json
from copy import deepcopy
from uuid import uuid4, UUID

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from service_settings import load_settings
from service_contracts.plan_review import canonical
from service_contracts.execution_repair import RepairResponse, RepairInteractionData, validate_repair_action
from service_contracts.events import EventContext, ExecutorEvent
from service_contracts.user_resume import resume_identity, resume_envelope
from agent_service.agents.analysis.planning.catalog import AssetCatalog
from agent_service.agents.analysis.planning.runtime import PlanningRuntime
from agent_service.agents.analysis.planning.graph import build_planning_graph
from agent_service.agents.analysis.execution.repair_policy import proposal_snapshot, source_info, verify_candidate
from agent_service.agents.analysis.tests.test_agentic_execution import LocalExecutor, Bindings
from api_service.agent_worker.langgraph_adapter import LangGraphEventAdapter


class FixtureCatalog(AssetCatalog):
    """Test-only registered functions; never added to the deployed catalogue."""
    def __init__(self):
        super().__init__()
        sources={
            'repair_load':"def repair_load():\n    globals()['repair_load_calls'] = globals().get('repair_load_calls', 0) + 1\n    return {'values': [2, 4, 6]}\n",
            'repair_transform':"def repair_transform(data, divisor=0):\n    if divisor <= 0:\n        raise ValueError('divisor must be positive')\n    return {'values': [x / divisor for x in data['values']]}\n",
            'repair_finish':"def repair_finish(data):\n    return {'sum': sum(data['values'])}\n",
            'registered_transform':"def registered_transform(data, divisor=1):\n    return {'values': [x / divisor for x in data['values']]}\n",
        }
        for key,code in sources.items():
            self.sources[key],self.metadata['tools'][key]=source_info(code)
        self.metadata['skills']['repair_demo']={'name':'repair_demo','description':'Test only transform Skill', 'tools':list(sources),'limitations':[]}
        self.skill_sources['repair_demo']={'markdown':'# Test transform\nUse positive divisors. Keep values contract.', 'sha256':'test'}
        self.revision=hashlib.sha256(canonical({'tools':self.sources,'skills':self.skill_sources}).encode()).hexdigest()


def document(level=1, attempts=2):
    return {'schema_version':'2.0-draft','workflow_id':'repair_fixture','definition_version':1,
        'name':'등록 데이터 변환 시험','description':'Test only','goal':'Transform the supplied values and compute their sum.',
        'tags':[],'inputs':{},'decisions':[],
        'steps':[
            {'id':'load','skill_id':'repair_demo','tool_id':'repair_load','description':'Load test values','depends_on':[],'arguments':{}},
            {'id':'transform','skill_id':'repair_demo','tool_id':'repair_transform','description':'Transform test values','depends_on':['load'],
             'arguments':{'data':{'source':'step_output','step_id':'load','selector':[]},'divisor':{'source':'literal','value':0}},
             'parameter_controls':{'divisor':{'editable':True,'value_schema':{'type':'integer','minimum':-10,'maximum':10}}}},
            {'id':'finish','skill_id':'repair_demo','tool_id':'repair_finish','description':'Compute sum','depends_on':['transform'],
             'arguments':{'data':{'source':'step_output','step_id':'transform','selector':[]}}}],
        'execution':{'mode':'MULTI','repair_level':level,'max_repair_attempts':attempts,'review_mode':'decision_boundary'},
        'expected_outputs':[{'id':'sum_result','kind':'analysis_result','description':'Sum','required':True,
             'source':{'source':'step_output','step_id':'finish','selector':['sum']},'format':'native'}]}


def correction(level, payload, *, value=2, needs_input=False):
    snapshot=payload['repair_context'].get('execution_snapshot') or payload['repair_context']['approved_snapshot']
    steps=deepcopy(snapshot['steps']); target=next(s for s in steps if s['id']=='transform')
    response={'can_repair':True,'summary':'실패한 변환 단계만 보정하고 합계 계산을 이어갑니다.',
              'reason':'Actual failed output requires a positive divisor.','evidence_steps':['transform'],
              'needs_user_input':needs_input}
    if level==1:
        args=deepcopy(target['arguments']);args['divisor']={'source':'literal','value':value}
        response['argument_changes']=[{'step_id':'transform','arguments':args}]
    elif level==2:
        code=snapshot['tool_sources'][target['tool_id']]['code'].replace("if divisor <= 0:","if divisor is None:")
        code=code.replace("return {'values': [x / divisor", "divisor = divisor if divisor > 0 else 1\n    return {'values': [x / divisor")
        response['source_changes']=[{'step_id':'transform','code':code}]
    elif level==3:
        target['tool_id']='registered_transform';target['arguments']['divisor']={'source':'literal','value':value}
        response['replacement_steps']=steps
    elif level==4:
        target['tool_id']='custom.transformed_values';target['arguments']['divisor']={'source':'literal','value':value}
        response['replacement_steps']=steps
        response['source_changes']=[{'step_id':'transform','code':"def transform_values(data, divisor=1):\n    return {'values': [x / divisor for x in data['values']]}\n"}]
    return RepairResponse.model_validate(response)


class PartialResultExecutor(LocalExecutor):
    async def request(self,*args,**kwargs):
        result=await super().request(*args,**kwargs)
        if self.events and self.events[-1]['event_type']=='execution.operation_completed':
            payload=self.events[-1]['payload']
            payload['step_results']=[s for s in payload['step_results'] if s.get('result_ref')]
            if payload['status']=='FAILED':payload['error']['code']='OPERATION_STEP_FAILED'
        return result


async def make_runtime(settings, executor, *, level=1, attempts=2, proposed_level=None, needs_input=False, wrong=False, second_failure=False):
    runtime=PlanningRuntime(settings,catalog=FixtureCatalog(),executor=executor,bindings=Bindings())
    from agent_service.agents.analysis.agent_builders.conversation.agent import reply_schema
    definition=document(level,attempts)
    if second_failure:
        code="def repair_finish(data, scale=0):\n    if scale <= 0:\n        raise ValueError('scale must be positive')\n    return {'sum': sum(data['values']) * scale}\n"
        runtime.catalog.sources['repair_finish'],runtime.catalog.metadata['tools']['repair_finish']=source_info(code)
        runtime.catalog.revision=hashlib.sha256(canonical({'tools':runtime.catalog.sources,'skills':runtime.catalog.skill_sources}).encode()).hexdigest()
        definition['steps'][-1]['arguments']['scale']={'source':'literal','value':0}
        definition['steps'][-1]['parameter_controls']={'scale':{'editable':True,'value_schema':{'type':'integer','minimum':0,'maximum':10}}}
    async def respond(*args):
        return reply_schema(runtime.catalog,1)(kind='plans',message='Test correction plan',plans=[{'definition':definition}])
    runtime.respond=respond
    original=runtime.execution_role
    calls=[]
    async def role(name,state,payload):
        if name=='repair':
            calls.append(deepcopy(payload))
            if second_failure and state['failed_step_ids']==['finish']:
                snapshot=payload['repair_context'].get('execution_snapshot') or payload['repair_context']['approved_snapshot']
                arguments=deepcopy(snapshot['steps'][-1]['arguments']);arguments['scale']={'source':'literal','value':1}
                return RepairResponse(can_repair=True,summary='합계 단계의 실행 연결만 보정합니다.',reason='scale must be positive',
                    evidence_steps=['finish'],argument_changes=[{'step_id':'finish','arguments':arguments}])
            return correction(proposed_level or level,payload,value=-len(calls) if wrong else 2,needs_input=needs_input)
        return await original(name,state,payload)
    runtime.execution_role=role
    return runtime,calls


async def scenario(tmp_path,monkeypatch,**options):
    settings=load_settings(config={'MODEL_PROVIDER':'mock','EXECUTOR_SOURCE_TYPE':'INLINE','EXECUTOR_RUNTIME_PROFILE':'default',
        'EXECUTOR_SHARED_RESULT_ROOT':str(tmp_path),'EXECUTOR_BASE_URL':'http://test','EXECUTOR_SUBMIT_ENABLED':True},environ={}).agent
    executor=PartialResultExecutor(tmp_path)
    runtime,calls=await make_runtime(settings,executor,**options)
    graph=build_planning_graph(runtime,checkpointer=InMemorySaver())
    value={k:str(uuid4()) for k in ('user_id','project_id','session_id','run_id')}
    value.update(user_request='Transform values',model_selection=runtime.models.select().model_dump())
    config={'configurable':{'thread_id':value['session_id']}}
    state=await graph.ainvoke(value,config,durability='sync')
    view=state['plan_views'][0]
    async def resume(command):
        boundary=(await graph.aget_state(config)).tasks[0].interrupts[0]
        identity=resume_identity(str(uuid4()),boundary.id,command)
        return await graph.ainvoke(Command(resume={boundary.id:resume_envelope(identity,command)}),config,durability='sync')
    state=await resume({'resume':{'action':'approve_plan','plan_id':view['plan_id'],'plan_revision':1}})
    import api_service.services.graph_crud_persistence as persistence
    async def persist(*args,**kwargs):return args[0]
    monkeypatch.setattr(persistence,'persist_graph_state',persist)
    async def deliver(event):
        current=(await graph.aget_state(config)).values
        context=EventContext(namespace='test',session_id=value['session_id'],task_id=current['task_id'],execution_id=UUID(executor.id),
            command_id=uuid4(),event=ExecutorEvent.model_validate(event))
        await LangGraphEventAdapter(graph)(context)
        return context,(await graph.aget_state(config)).values
    return runtime,executor,graph,config,state,calls,deliver,resume


def approve(review, *, escalation=False, reject=False):
    return {'resume':{'action':'reject_repair' if reject else 'approve_repair','interaction_id':review['interaction_id'],
        'revision':review['revision'],'proposal_sha256':review['payload']['proposal_sha256'],'allow_policy_escalation':escalation}}


@pytest.mark.asyncio
@pytest.mark.parametrize('level',[1,2,3,4])
async def test_levels_execute_correction_preserve_successful_anchor_finalize_and_receipt_replay(tmp_path,monkeypatch,level):
    runtime,executor,graph,config,state,calls,deliver,resume=await scenario(tmp_path,monkeypatch,level=level)
    original=deepcopy(state['approved_snapshot'])
    _,state=await deliver(executor.events[0])
    assert state['completed_steps']==['load'] and len(calls)==1
    if level==3:
        assert len(executor.calls)==1 and state['repair_review']['payload']['required_level']==3
        RepairInteractionData.model_validate(state['repair_review'])
        state=await resume(approve(state['repair_review']))
    assert len(executor.calls)==2 and executor.globals['repair_load_calls']==1
    assert state['repair_attempts']==1 and state['approved_snapshot']==original
    assert [s['sequence'] for s in executor.calls[1][1]['spec']['steps']]==[3,4]
    assert executor.calls[1][1]['expected_version']==3
    assert state['submitted_steps'][0]['plan_step_id']=='transform'
    ctx,state=await deliver(executor.events[1])
    assert executor.calls[-1][0].endswith('/finalize')
    await LangGraphEventAdapter(graph)(ctx)
    assert len(executor.calls)==3
    _,state=await deliver(executor.event('execution.completed',{'status':'SUCCEEDED','error':None}))
    assert state['final_response']['status']=='analysis_completed'
    assert state['final_response']['repair']['attempts']==1
    assert state['final_response']['repair']['workflow_eligible']==(level in (1,3))
    assert state['observations'][1]['status']=='FAILED' and state['observations'][2]['status']=='NOT_RUN'
    assert state['observations'][-1]['summary']['items']['sum']==(12 if level==2 else 6)
    assert not (await graph.aget_state(config)).next


@pytest.mark.asyncio
async def test_escalation_requires_explicit_approval_and_user_can_reject(tmp_path,monkeypatch):
    runtime,executor,graph,config,state,calls,deliver,resume=await scenario(tmp_path,monkeypatch,level=1,proposed_level=2)
    _,state=await deliver(executor.events[0]);review=state['repair_review']
    assert review['payload']['requires_policy_escalation'] and len(executor.calls)==1
    with pytest.raises(ValueError,match='escalation'):validate_repair_action(review,approve(review)['resume'])
    action=approve(review,escalation=True)['resume']
    for invalid in ({**action,'revision':2},{**action,'proposal_sha256':'0'*64},{**action,'unknown':'extra'}):
        with pytest.raises(ValueError):validate_repair_action(review,invalid)
    state=await resume(approve(review,reject=True))
    assert executor.calls[-1][0].endswith('/cancel') and state['repair_attempts']==0
    _,state=await deliver(executor.event('execution.completed',{'status':'CANCELLED','error':None}))
    assert state['final_response']['status']=='analysis_failed' and state['repair_stop_reason']=='user_rejected'


@pytest.mark.asyncio
async def test_approved_escalation_and_repair_timeout_close_human_wait(tmp_path,monkeypatch):
    runtime,executor,graph,config,state,calls,deliver,resume=await scenario(tmp_path,monkeypatch,level=1,proposed_level=2)
    _,state=await deliver(executor.events[0]);state=await resume(approve(state['repair_review'],escalation=True))
    assert state['repair_authorized_level']==2 and len(executor.calls)==2
    ctx,state=await deliver(executor.events[1]);assert executor.calls[-1][0].endswith('/finalize')


@pytest.mark.asyncio
async def test_terminal_timeout_during_repair_wait_never_submits_candidate(tmp_path,monkeypatch):
    runtime,executor,graph,config,state,calls,deliver,resume=await scenario(tmp_path,monkeypatch,level=1,needs_input=True)
    _,state=await deliver(executor.events[0]);assert state['repair_review']
    _,state=await deliver(executor.event('execution.completed',{'status':'FAILED','error':{'code':'TIMEOUT','message':'expired'}}))
    assert len(executor.calls)==1 and state['final_response']['status']=='analysis_failed'
    assert state['repair_candidate'] is None and state['repair_review'] is None
    assert state['repair_stop_reason']=='executor_terminal_during_repair'


@pytest.mark.asyncio
async def test_bad_repairs_exhaust_run_wide_budget_without_replaying_successful_steps(tmp_path,monkeypatch):
    runtime,executor,graph,config,state,calls,deliver,resume=await scenario(tmp_path,monkeypatch,level=1,attempts=2,wrong=True)
    for event in [executor.events[0]]:
        _,state=await deliver(event)
    _,state=await deliver(executor.events[1])
    assert state['repair_attempts']==2 and len(calls)==2
    _,state=await deliver(executor.events[2])
    assert executor.calls[-1][0].endswith('/cancel') and state['repair_stop_reason']=='attempts_exhausted'
    assert executor.globals['repair_load_calls']==1
    assert not any(url.endswith('/finalize') for url,_ in executor.calls)


@pytest.mark.asyncio
async def test_invalid_proposal_does_not_submit_or_count_a_repair(tmp_path,monkeypatch):
    runtime,executor,graph,config,state,calls,deliver,resume=await scenario(tmp_path,monkeypatch)
    async def invalid(name,state,payload):
        answer=correction(1,payload)
        answer.argument_changes[0].arguments['divisor']={'source':'literal','value':1000}
        return answer
    runtime.execution_role=invalid
    _,state=await deliver(executor.events[0])
    assert state['repair_stop_reason']=='invalid_repair_proposal' and state['repair_attempts']==0
    assert executor.calls[-1][0].endswith('/cancel') and len(executor.calls)==2


@pytest.mark.asyncio
async def test_pure_policy_rejects_completed_edit_unregistered_asset_bad_signature_and_tampering(tmp_path,monkeypatch):
    runtime,executor,graph,config,state,calls,deliver,resume=await scenario(tmp_path,monkeypatch,needs_input=True)
    _,state=await deliver(executor.events[0]);context=calls[0]['repair_context']
    valid=correction(1,calls[0])
    bad=valid.model_copy(deep=True);bad.argument_changes[0].step_id='load'
    with pytest.raises(ValueError,match='immutable'):proposal_snapshot(context,bad,runtime.catalog,level_limit=4)
    bad=correction(3,calls[0]);bad.replacement_steps[1]['tool_id']='unknown'
    with pytest.raises(ValueError,match='registered'):proposal_snapshot(context,bad,runtime.catalog,level_limit=4)
    bad=correction(2,calls[0]);bad.source_changes[0].code=bad.source_changes[0].code.replace('divisor=0','divisor=1')
    with pytest.raises(ValueError,match='signature'):proposal_snapshot(context,bad,runtime.catalog,level_limit=4)
    bad=correction(4,calls[0])
    with pytest.raises(ValueError,match='capability'):proposal_snapshot(context,bad,runtime.catalog,level_limit=2)
    candidate=deepcopy(state['repair_candidate']);candidate['snapshot']['steps'][1]['description']='tampered'
    with pytest.raises(ValueError,match='changed'):verify_candidate(candidate,state)
    assert len(executor.calls)==1


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['prompt_json','provider_json_schema'])
async def test_repair_create_agent_semantic_middleware_corrects_binding_without_sync_transport(tmp_path,monkeypatch,mode):
    import httpx
    from langchain_openai import ChatOpenAI
    from agent_service.context import AgentContext
    from agent_service.agents.analysis.agent_builders.execution_repair.agent import build_agent
    from agent_service.agents.analysis.tests.test_agent_middleware import response
    runtime,executor,graph,config,state,calls,deliver,resume=await scenario(tmp_path,monkeypatch,needs_input=True)
    _,state=await deliver(executor.events[0]);payload=calls[0];requests=[]
    def validate_response(value,request):
        proposal_snapshot(payload['repair_context'],value,runtime.catalog,level_limit=4)
    async def handle(request):
        requests.append(json.loads(request.content))
        answer=correction(1,payload,value=1000 if len(requests)==1 else 2)
        return response(answer.model_dump_json())
    def no_sync(request):raise AssertionError('synchronous model HTTP called')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as ac:
        with httpx.Client(transport=httpx.MockTransport(no_sync)) as sc:
            model=ChatOpenAI(model='test',api_key='test',base_url='http://llm.invalid/v1',max_retries=0,http_async_client=ac,http_client=sc)
            result=await build_agent(model,runtime.catalog,structured_output_mode=mode,validate_response=validate_response).ainvoke(payload,
                context=AgentContext(project_system_prompt='PROJECT REPAIR RULE'))
    assert result.argument_changes[0].arguments['divisor']['value']==2 and len(requests)==2
    assert all(r['messages'][0]['content'].count('PROJECT REPAIR RULE')==1 for r in requests)
    assert 'schema' in requests[1]['messages'][-1]['content']
    assert len(executor.calls)==1


def test_repair_defaults_respect_workflow_config_then_hitl_and_service_ceiling():
    from service_contracts.plan_review import new_review,patch_review
    catalog=FixtureCatalog();doc=document();doc['execution'].pop('repair_level');doc['execution'].pop('max_repair_attempts')
    policy={'allowed_modes':['MULTI'],'repair_level_limit':3,'max_repair_attempts_limit':2,'default_repair_level':2,'default_repair_attempts':2}
    review=new_review(doc,{},catalog.metadata,policy)
    assert review['document']['execution']['repair_level']==2 and review['document']['execution']['max_repair_attempts']==2
    assert not any(k.startswith('default_') for k in review['policy'])
    explicit=document(level=0,attempts=0);review=new_review(explicit,{},catalog.metadata,policy)
    assert review['document']['execution']['repair_level']==0
    action={'action':'approve_plan','plan_id':review['plan_id'],'plan_revision':1,'execution_overrides':{'repair_level':1,'max_repair_attempts':1}}
    assert patch_review(review,action,datasets={},context={})['document']['execution']['repair_level']==1
    action['execution_overrides']['repair_level']=4
    with pytest.raises(ValueError,match='limit'):patch_review(review,action,datasets={},context={})
    settings=load_settings(config={'AGENT_REPAIR_LEVEL':2,'AGENT_REPAIR_LEVEL_LIMIT':3,'AGENT_MAX_REPAIR_ATTEMPTS':2},environ={})
    assert settings.agent.agent_repair_level==2
    for config in ({'AGENT_REPAIR_LEVEL':3,'AGENT_REPAIR_LEVEL_LIMIT':2},{'AGENT_MAX_REPAIR_ATTEMPTS':11}):
        with pytest.raises(ValueError):load_settings(config=config,environ={})


@pytest.mark.asyncio
@pytest.mark.parametrize('code,incomplete',[('OPERATION_COMPLETION_FAILED',False),('OPERATION_CANCELLED',False),('OPERATION_STEP_FAILED',True)])
async def test_non_code_or_incomplete_failure_is_never_repaired(tmp_path,monkeypatch,code,incomplete):
    runtime,executor,graph,config,state,calls,deliver,resume=await scenario(tmp_path,monkeypatch)
    event=deepcopy(executor.events[0]);event['payload']['error']['code']=code
    if incomplete:
        # No result ref means no complete code-error evidence. Avoid fabricating a partial manifest.
        event['payload']['step_results'][1]['result_ref']=None
    _,state=await deliver(event)
    assert not calls and state['repair_attempts']==0 and executor.calls[-1][0].endswith('/cancel')


@pytest.mark.asyncio
async def test_failure_receipt_replay_at_human_repair_wait_does_not_regenerate_or_submit(tmp_path,monkeypatch):
    runtime,executor,graph,config,state,calls,deliver,resume=await scenario(tmp_path,monkeypatch,needs_input=True)
    ctx,state=await deliver(executor.events[0]);candidate=deepcopy(state['repair_candidate'])
    await LangGraphEventAdapter(graph)(ctx)
    after=(await graph.aget_state(config)).values
    assert len(calls)==1 and len(executor.calls)==1 and after['repair_candidate']==candidate


@pytest.mark.asyncio
async def test_discovery_synthesis_uses_repair_schema_not_conversation_reply(tmp_path,monkeypatch):
    import httpx
    from langchain_openai import ChatOpenAI
    from agent_service.agents.analysis.agent_builders.execution_repair.agent import build_agent
    from agent_service.agents.analysis.tests.test_agent_middleware import response
    runtime,executor,graph,config,state,calls,deliver,resume=await scenario(tmp_path,monkeypatch,needs_input=True)
    _,state=await deliver(executor.events[0]);payload=calls[0];requests=[]
    async def handle(request):
        requests.append(json.loads(request.content))
        if len(requests)==1:
            return httpx.Response(200,json={'id':'tools','object':'chat.completion','created':0,'model':'test',
                'choices':[{'index':0,'finish_reason':'tool_calls','message':{'role':'assistant','content':'',
                    'tool_calls':[{'id':'read','type':'function','function':{'name':'read_skill','arguments':'{"skill_id":"repair_demo"}'}}]}}]})
        return response(correction(1,payload).model_dump_json())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        model=ChatOpenAI(model='test',api_key='test',base_url='http://llm.invalid/v1',max_retries=0,http_async_client=client)
        result=await build_agent(model,runtime.catalog,discovery_max_rounds=1).ainvoke(payload)
    assert result.can_repair and len(requests)==2
    assert len(requests[0]['tools'])==2 and not requests[1].get('tools')
    assert 'Return exactly the RepairResponse JSON schema' in requests[1]['messages'][0]['content']
    assert 'honest answer/clarification' not in requests[1]['messages'][0]['content']


@pytest.mark.asyncio
async def test_noop_source_readonly_literal_and_trusted_context_boundaries(tmp_path,monkeypatch):
    from agent_service.agents.analysis.execution.repair_policy import seal
    runtime,executor,graph,config,state,calls,deliver,resume=await scenario(tmp_path,monkeypatch,needs_input=True)
    _,state=await deliver(executor.events[0]);payload=calls[0];context=payload['repair_context']
    raw=correction(2,payload);raw.source_changes[0].code=context['approved_snapshot']['tool_sources']['repair_transform']['code']
    with pytest.raises(ValueError,match='must change'):proposal_snapshot(context,raw,runtime.catalog,level_limit=4)
    readonly=deepcopy(context)
    for steps in (readonly['approved_snapshot']['steps'],readonly['approved_snapshot']['document']['steps']):
        steps[1]['parameter_controls']['divisor']['editable']=False
    seal(readonly['approved_snapshot'])
    candidate=proposal_snapshot(readonly,correction(1,{'repair_context':readonly}),runtime.catalog,level_limit=4)
    assert candidate['required_level']==1 and candidate['requires_approval']
    trusted=deepcopy(context)
    for steps in (trusted['approved_snapshot']['steps'],trusted['approved_snapshot']['document']['steps']):
        steps[1]['arguments']['data']={'source':'system_context','key':'project_id'}
    seal(trusted['approved_snapshot'])
    raw=correction(1,{'repair_context':trusted});raw.argument_changes[0].arguments['data']={'source':'literal','value':{'values':[1]}}
    with pytest.raises(ValueError,match='Trusted'):proposal_snapshot(trusted,raw,runtime.catalog,level_limit=4)


@pytest.mark.asyncio
async def test_transport_failure_never_turns_into_a_repair_or_cancel_success(tmp_path,monkeypatch):
    import httpx
    runtime,executor,graph,config,state,calls,deliver,resume=await scenario(tmp_path,monkeypatch)
    async def unavailable(*args):raise httpx.ConnectError('test-only unavailable model')
    runtime.execution_role=unavailable
    with pytest.raises(httpx.ConnectError):await deliver(executor.events[0])
    assert len(executor.calls)==1
    state=(await graph.aget_state(config)).values
    assert state['repair_attempts']==0 and not state['final_response']


@pytest.mark.asyncio
@pytest.mark.parametrize('budget',[1,2])
async def test_budget_is_shared_across_distinct_failed_steps_and_preserves_all_successful_objects(tmp_path,monkeypatch,budget):
    runtime,executor,graph,config,state,calls,deliver,resume=await scenario(tmp_path,monkeypatch,attempts=budget,second_failure=True)
    _,state=await deliver(executor.events[0])
    _,state=await deliver(executor.events[1])
    assert state['completed_steps']==['load','transform']
    assert executor.globals['repair_load_calls']==1
    if budget==1:
        assert len(calls)==1 and executor.calls[-1][0].endswith('/cancel') and state['repair_stop_reason']=='attempts_exhausted'
    else:
        assert len(calls)==2 and state['repair_attempts']==2
        assert len(executor.calls[2][1]['spec']['steps'])==1
        assert executor.calls[2][1]['spec']['steps'][0]['sequence']==5
        assert state['submitted_steps'][0]['plan_step_id']=='finish'
        _,state=await deliver(executor.events[2])
        assert state['completed_steps']==['load','transform','finish'] and executor.calls[-1][0].endswith('/finalize')


@pytest.mark.asyncio
async def test_level_four_authorizes_registered_replan_without_unnecessary_second_approval(tmp_path,monkeypatch):
    runtime,executor,graph,config,state,calls,deliver,resume=await scenario(tmp_path,monkeypatch,level=4,proposed_level=3)
    _,state=await deliver(executor.events[0])
    assert state['repair_review'] is None and state['repair_attempts']==1 and len(executor.calls)==2
    assert state['repair_history'][-1]['required_level']==3 and state['repair_authorized_level']==4
    _,state=await deliver(executor.events[1])
    assert executor.calls[-1][0].endswith('/finalize')
