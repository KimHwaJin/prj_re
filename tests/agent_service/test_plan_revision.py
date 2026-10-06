"""Durable feedback, clarification, execution-local code and authorization boundaries."""
from copy import deepcopy
from dataclasses import replace
import json
from uuid import uuid4, UUID

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from dtest.settings.loader import load_settings
from dtest.contracts.user_resume import resume_identity, resume_envelope
from dtest.contracts.plan_interaction import validate_plan_revision, ResumeRequest
from dtest.contracts.events import EventContext, ExecutorEvent
from dtest.contracts.workflow_validation import validate
from tests.agent_service.test_agentic_repair import FixtureCatalog, document, PartialResultExecutor
from tests.agent_service.test_agentic_execution import Bindings
from dtest.agent_service.agents.analysis.agent_builders.conversation.agent import reply_schema
from dtest.agent_service.agents.analysis.planning.runtime import PlanningRuntime
from dtest.agent_service.agents.analysis.planning.graph import build_planning_graph
from dtest.agent_service.agents.analysis.planning.proposals import RevisionReply, prepare_review, LocalFunction
from dtest.application.runs.graph_invocation import GraphInvocation


SOURCE = "def revision_transform(data, divisor=2):\n    return {'values': [x / divisor for x in data['values']]}\n"


def revision_reply(*, free=True, missing=False, multiple=False):
    doc = document(0,0)
    doc['steps'][1]['arguments']['divisor']['value'] = 2
    functions = []
    if free:
        doc['steps'][1]['tool_id'] = 'custom.revised_transform'
        functions = [LocalFunction.from_code(step_id='transform',code=SOURCE,reason='요청한 변환을 실행별 함수로 작성합니다.').model_dump()]
    if not free:
        doc['steps'][1]['tool_id']='registered_transform'
    if missing:
        doc['inputs']['label']={'title':'Label','description':'Required label','kind':'parameter','required':True,'editable':True,'value_schema':{'type':'string'}}
    proposal={'definition':doc,'functions':functions}
    return RevisionReply(kind='plans',message='변경 요청에 맞게 계획을 다시 준비했습니다.',plans=[proposal]*(2 if multiple else 1))


async def setup_revision(tmp_path, *, approval=True, enabled=True, execution=False, limit=5, replies=None):
    settings=load_settings(config={'MODEL_PROVIDER':'mock','EXECUTOR_SOURCE_TYPE':'INLINE','EXECUTOR_BASE_URL':'http://test',
        'EXECUTOR_SHARED_RESULT_ROOT':str(tmp_path),'EXECUTOR_RUNTIME_PROFILE':'default','EXECUTOR_SUBMIT_ENABLED':execution,
        'AGENT_FREE_PLAN_REQUIRE_APPROVAL':approval,'AGENT_FREE_PLAN_ENABLED':enabled,'AGENT_MAX_PLAN_REVISIONS':limit},environ={}).agent
    executor=PartialResultExecutor(tmp_path)
    runtime=PlanningRuntime(settings,catalog=FixtureCatalog(),executor=executor,bindings=Bindings())
    async def respond(*args):return reply_schema(runtime.catalog,1)(kind='plans',message='초기 등록 계획',plans=[{'definition':document(0,0)}])
    runtime.respond=respond
    calls=[]
    async def revise(state,*args):
        calls.append(deepcopy(state))
        return replies[len(calls)-1] if replies else revision_reply()
    runtime.revise=revise
    saver=InMemorySaver(); graph=build_planning_graph(runtime,checkpointer=saver)
    value={k:str(uuid4()) for k in ('user_id','project_id','session_id','run_id')}
    value.update(user_request='값을 변환하고 합계를 내줘',model_selection=runtime.models.select().model_dump())
    config={'configurable':{'thread_id':value['session_id']}}
    state=await graph.ainvoke(value,config,durability='sync')
    async def resume(action):
        boundary=(await graph.aget_state(config)).tasks[0].interrupts[0]
        command={'resume':action};identity=resume_identity(str(uuid4()),boundary.id,command)
        return await graph.ainvoke(Command(resume={boundary.id:resume_envelope(identity,command)}),config,durability='sync')
    return runtime,executor,graph,saver,config,state,calls,resume


def replan(state,feedback='모든 후보가 마음에 안 들어. 다른 변환 방법으로 해줘'):
    form=state['interaction_data']
    return {'action':'answer_clarification' if form['kind']=='planning_question' else 'replan',
            'interaction_id':form['interaction_id'],'revision':form['revision'],'feedback':feedback}


@pytest.mark.asyncio
async def test_replan_clarification_restart_revision_budget_and_retired_plan(tmp_path):
    question=RevisionReply(kind='clarification',message='어떤 변환을 원하시나요?',plans=[])
    runtime,executor,graph,saver,config,state,calls,resume=await setup_revision(tmp_path,replies=[question,revision_reply(free=False)],limit=2)
    original=state['plan_views'][0]
    state=await resume(replan(state))
    assert state['interaction_data']['kind']=='planning_question' and state['planning_revision_count']==1
    assert state['public_run_id']==calls[0]['public_run_id'] and not executor.calls
    graph2=build_planning_graph(runtime,checkpointer=saver)
    boundary=(await graph2.aget_state(config)).tasks[0].interrupts[0]
    command={'resume':replan(state,'用 divisor=2 변환')};identity=resume_identity(str(uuid4()),boundary.id,command)
    state=await graph2.ainvoke(Command(resume={boundary.id:resume_envelope(identity,command)}),config,durability='sync')
    assert state['planning_revision_count']==2 and len(state['planning_feedback'])==2
    assert calls[1]['planning_previous_reviews'][0]['plan_id']==original['plan_id']
    assert state['plan_views'][0]['plan_id']!=original['plan_id']
    with pytest.raises(ValueError,match='limit'):validate_plan_revision(state['interaction_data'],replan(state),count=2,limit=2)
    state=await resume({'action':'approve_plan','plan_id':original['plan_id'],'plan_revision':1})
    assert state['review_error']=='Unknown plan' and not state['approved_snapshot']
    chosen=state['plan_views'][0]
    state=await resume({'action':'approve_plan','plan_id':chosen['plan_id'],'plan_revision':1})
    assert state['final_response']['status']=='plan_approved' and not executor.calls
    assert state['approved_snapshot']['workflow_eligible'] is True


@pytest.mark.asyncio
@pytest.mark.parametrize('approval',[True,False])
async def test_free_plan_approval_setting_actual_compiler_execution_and_terminal(tmp_path,monkeypatch,approval):
    runtime,executor,graph,saver,config,state,calls,resume=await setup_revision(tmp_path,approval=approval,execution=True)
    before=deepcopy(runtime.catalog.sources)
    state=await resume(replan(state))
    if approval:
        assert not executor.calls and not state['approved_snapshot']
        view=state['plan_views'][0]
        assert view['execution_kind']=='free_code' and view['workflow_eligible'] is False
        assert 'def revision_transform' not in json.dumps(state['interaction_data'])
        state=await resume({'action':'approve_plan','plan_id':view['plan_id'],'plan_revision':1,
            'step_changes':[{'step_id':'transform','parameter':'divisor','value':3}]})
    else:
        assert state['approved_snapshot']['approval_mode']=='configuration'
        assert any(e['envelope']['data'].get('resolution')=='auto_approved' for e in state['public_events'])
    assert len(executor.calls)==1 and runtime.catalog.sources==before
    assert state['approved_snapshot']['tool_sources']['custom.revised_transform']['code'].startswith('def revision_transform')
    import dtest.application.runs.persistence.graph as persistence
    async def persist(*args,**kwargs):return args[0]
    monkeypatch.setattr(persistence,'persist_graph_state',persist)
    async def deliver(event):
        current=(await graph.aget_state(config)).values
        ctx=EventContext(namespace='test',session_id=current['session_id'],task_id=current['task_id'],execution_id=UUID(executor.id),
            command_id=uuid4(),event=ExecutorEvent.model_validate(event))
        await GraphInvocation(graph, model_validator=None).executor_resume(ctx)
    await deliver(executor.events[0])
    assert executor.calls[-1][0].endswith('/finalize')
    await deliver(executor.event('execution.completed',{'status':'SUCCEEDED','error':None}))
    final=(await graph.aget_state(config)).values
    assert final['terminal_event_seen'] and final['final_response']['status']=='analysis_completed'
    assert final['final_response']['repair']['workflow_eligible'] is False
    assert final['final_response']['observations'][-1]['summary']['items']['sum']==(4 if approval else 6)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['missing','multiple','registered'])
async def test_autoapproval_never_selects_candidate_or_fills_missing_input(tmp_path,mode):
    reply=revision_reply(free=mode!='registered',missing=mode=='missing',multiple=mode=='multiple')
    runtime,executor,graph,saver,config,state,calls,resume=await setup_revision(tmp_path,approval=False,replies=[reply])
    state=await resume(replan(state))
    assert state['__interrupt__'] and state['approved_snapshot'] is None and not executor.calls


@pytest.mark.asyncio
async def test_invalid_free_code_preserves_form_and_never_executes(tmp_path):
    runtime,executor,graph,saver,config,state,calls,resume=await setup_revision(tmp_path,enabled=False)
    original=state['plan_views'][0]['plan_id']
    state=await resume(replan(state))
    assert state['review_error'] and state['plan_views'][0]['plan_id']==original
    assert state['planning_revision_count']==1 and not executor.calls


def test_local_source_boundaries_workflow_is_still_registered_only(tmp_path):
    runtime=PlanningRuntime(load_settings(config={'MODEL_PROVIDER':'mock'},environ={}).agent,catalog=FixtureCatalog())
    context={k:str(uuid4()) for k in ('user_id','project_id','session_id')};context['planning_revision_count']=1
    proposal=revision_reply().plans[0]
    assert validate(proposal.definition,runtime.catalog.metadata)  # never globally registers custom functions
    review=prepare_review(proposal,runtime,context)
    assert review['execution_kind']=='free_code' and 'custom.revised_transform' not in runtime.catalog.sources
    for code in ['print(1)\n'+SOURCE,'@decorator\n'+SOURCE,'def f(a=__import__("os")):\n    return a\n']:
        invalid=proposal.model_copy(deep=True)
        with pytest.raises((ValueError,SyntaxError)):
            invalid.functions[0].code=code
            prepare_review(invalid,runtime,context)
    invalid=proposal.model_copy(deep=True);invalid.functions[0].origin_tool_id='repair_transform'
    with pytest.raises(ValueError,match='signature'):prepare_review(invalid,runtime,context)
    invalid=proposal.model_copy(deep=True);invalid.definition['steps'][1]['skill_id']='unknown'
    with pytest.raises(ValueError,match='registered Skill'):prepare_review(invalid,runtime,context)
    invalid=proposal.model_copy(deep=True);invalid.definition['tags']=['duplicate','duplicate']
    with pytest.raises(ValueError):prepare_review(invalid,runtime,context)
    invalid=proposal.model_copy(deep=True);invalid.definition['steps'][1]['description']=SOURCE
    with pytest.raises(ValueError,match='source'):prepare_review(invalid,runtime,context)
    with pytest.raises(ValueError):prepare_review(proposal,runtime,{**context,'planning_revision_count':0})


def test_revision_action_strict_limits_wrong_screen_and_config_priority():
    form={'interaction_id':str(uuid4()),'revision':1,'kind':'plan_review','status':'open'}
    action={'action':'replan','interaction_id':form['interaction_id'],'revision':1,'feedback':'another method'}
    assert validate_plan_revision(form,action,count=0,limit=5).feedback=='another method'
    for patch in [{'feedback':' '},{'revision':2},{'action':'answer_clarification'},{'code':SOURCE}]:
        with pytest.raises(ValueError):validate_plan_revision(form,{**action,**patch},count=0,limit=5)
    settings=load_settings(config={'AGENT_FREE_PLAN_REQUIRE_APPROVAL': False, 'AGENT_MAX_PLAN_REVISIONS': 3},
        environ={'AGENT_FREE_PLAN_REQUIRE_APPROVAL':'true'})
    assert settings.agent.agent_free_plan_require_approval is False and settings.agent.agent_max_plan_revisions==3
    with pytest.raises(ValueError):load_settings(config={'AGENT_MAX_PLAN_REVISIONS':0},environ={})


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['prompt_json','provider_json_schema'])
async def test_revision_create_agent_async_middleware_retries_structure_with_project_prompt(tmp_path,mode):
    import httpx
    from langchain_openai import ChatOpenAI
    from dtest.agent_service.context import AgentContext
    from dtest.agent_service.agents.analysis.agent_builders.plan_revision.agent import build_agent
    from tests.agent_service.test_agent_middleware import response
    runtime=PlanningRuntime(load_settings(config={'MODEL_PROVIDER':'mock'},environ={}).agent,catalog=FixtureCatalog())
    state={'planning_revision_count':1,'user_id':'u','project_id':'p','session_id':'s'}
    from dtest.agent_service.agents.analysis.planning.proposals import validate_revision_reply
    requests=[]
    async def handle(request):
        requests.append(json.loads(request.content));reply=revision_reply()
        if len(requests)==1:reply.plans[0].definition['steps'][1]['arguments']['not_a_parameter']={'source':'literal','value':1}
        return response(reply.model_dump_json())
    def no_sync(request):raise AssertionError('sync HTTP')
    def validator(reply,request):validate_revision_reply(reply,runtime,state)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as ac:
        with httpx.Client(transport=httpx.MockTransport(no_sync)) as sc:
            model=ChatOpenAI(model='test',api_key='test',base_url='http://llm.invalid/v1',max_retries=0,http_async_client=ac,http_client=sc)
            reply=await build_agent(model,runtime.catalog,structured_output_mode=mode,validate_response=validator).ainvoke({'feedback':'다르게 해줘'},context=AgentContext(project_system_prompt='PROJECT REVISION RULE'))
    assert len(requests)==2 and reply.plans[0].functions
    assert all(r['messages'][0]['content'].count('PROJECT REVISION RULE')==1 for r in requests)


@pytest.mark.asyncio
async def test_invalid_model_content_never_submits_and_transport_error_propagates(tmp_path):
    import httpx
    from dtest.agent_service.middleware.prompt_json import StructuredResponseError
    runtime,executor,graph,saver,config,state,calls,resume=await setup_revision(tmp_path)
    async def invalid(*args):raise StructuredResponseError('invalid private response')
    runtime.revise=invalid
    state=await resume(replan(state))
    assert state['planning_validation_error']=='invalid private response' and not executor.calls
    assert 'invalid private response' not in json.dumps(state['interaction_data'])
    async def unavailable(*args):raise httpx.ReadTimeout('model unavailable')
    runtime.revise=unavailable
    with pytest.raises(httpx.ReadTimeout):await resume(replan(state))
    assert not executor.calls


@pytest.mark.asyncio
async def test_native_schema_discovery_synthesis_omits_empty_tools_for_gateway(tmp_path):
    import httpx
    from dtest.agent_service.model import CompatibleChatOpenAI
    from dtest.agent_service.agents.analysis.agent_builders.plan_revision.agent import build_agent
    from tests.agent_service.test_agent_middleware import response
    from dtest.agent_service.context import AgentContext
    requests=[]
    async def handle(request):
        body=json.loads(request.content);requests.append(body)
        if len(requests)==1:
            reply=response('')
            payload=json.loads(reply.content)
            payload['choices'][0]['message']['tool_calls']=[{'id':'read-one','type':'function',
                'function':{'name':'read_skill','arguments':'{"skill_id":"repair_demo"}'}}]
            return httpx.Response(200,json=payload)
        if body.get('tools')==[]:return httpx.Response(400,json={'error':{'message':'tools must not be an empty array'}})
        return response(revision_reply().model_dump_json())
    catalog=FixtureCatalog()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as ac:
        model=CompatibleChatOpenAI(model='test',api_key='test',base_url='http://llm.invalid/v1',max_retries=0,http_async_client=ac)
        reply=await build_agent(model,catalog,structured_output_mode='provider_json_schema',discovery_max_rounds=1).ainvoke(
            {'feedback':'다른 방법'},context=AgentContext())
    assert len(requests)==2 and requests[0]['tools'] and 'tools' not in requests[1]
    assert requests[1]['response_format']['type']=='json_schema' and reply.kind=='plans'
    schema=requests[1]['response_format']['json_schema']['schema']
    assert 'steps' in schema['$defs']['ExecutionPlanDefinition']['properties']
    from jsonschema_rs import Draft202012Validator
    assert Draft202012Validator(RevisionReply.model_json_schema()).is_valid(reply.model_dump())


def test_base_plan_patch_preserves_bindings_policy_and_private_source(tmp_path):
    from dtest.agent_service.agents.analysis.planning.proposals import RevisionProposal
    runtime=PlanningRuntime(load_settings(config={'MODEL_PROVIDER':'mock'},environ={}).agent,catalog=FixtureCatalog())
    context={'user_id':'u','project_id':'p','session_id':'s','planning_revision_count':1}
    original=prepare_review(revision_reply(free=False).plans[0],runtime,context)
    context['reviews']=[original]
    proposal=RevisionProposal(base_plan_id=original['plan_id'],patches=[{'step_id':'transform','tool_id':'custom.revised_transform'}],
        functions=revision_reply().plans[0].functions)
    changed=prepare_review(proposal,runtime,context)
    assert changed['document']['steps'][2]==original['document']['steps'][2]
    assert changed['document']['execution']==original['document']['execution']
    assert changed['document']['steps'][1]['arguments']==original['document']['steps'][1]['arguments']
    assert changed['input_origins']==original['input_origins']
    context['reviews']=[changed];context['planning_revision_count']=2
    second=prepare_review(RevisionProposal(base_plan_id=changed['plan_id'],patches=[{'step_id':'transform',
        'parameter_changes':[{'name':'divisor','binding':{'source':'literal','value':3}}]}]),runtime,context)
    assert second['local_sources']==changed['local_sources'] and second['execution_kind']=='free_code'
    assert second['document']['steps'][1]['arguments']['divisor']['value']==3
    assert changed['document']['steps'][1]['arguments']['divisor']['value']==2
    with pytest.raises(ValueError,match='retired'):prepare_review(proposal,runtime,context)
    invalid=RevisionProposal(base_plan_id=changed['plan_id'],patches=[{'step_id':'finish','removed_parameters':['data']}])
    with pytest.raises(ValueError):prepare_review(invalid,runtime,context)
    invalid=RevisionProposal(base_plan_id=changed['plan_id'],patches=[{'step_id':'transform','tool_id':'custom.unknown'}])
    with pytest.raises(ValueError,match='Unknown execution-local'):prepare_review(invalid,runtime,context)
