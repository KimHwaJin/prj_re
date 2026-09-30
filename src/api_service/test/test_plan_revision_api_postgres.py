"""HTTP/SQL revision admission, durable saver rebuild and SSE privacy."""
from copy import deepcopy
from dataclasses import replace
import json
from uuid import uuid4

import pytest
import service_settings
from api_service.test.test_planning_api_postgres import planning, test_config, submit, execute, read
from api_service.services.agent_graph_service import runtime as graph_runtime
from agent_service.agents.analysis.tests.test_plan_revision import setup_revision, revision_reply, SOURCE
from agent_service.agents.analysis.planning.graph import build_planning_graph
from service_contracts.plan_interaction import ClarificationEvent, PlanningTransitionEvent


@pytest.mark.asyncio
async def test_feedback_admission_stale_tokens_replay_question_restart_and_free_approval(planning,tmp_path,monkeypatch):
    from agent_service.agents.analysis.planning.proposals import RevisionReply
    h=planning
    question=RevisionReply(kind='clarification',message='어떤 변환 방법을 원하시나요?',plans=[])
    runtime,executor,unused,saver,config,unused_state,calls,resume=await setup_revision(tmp_path,
        replies=[question,revision_reply()],limit=2)
    settings=service_settings.get_settings()
    monkeypatch.setattr(service_settings,'_snapshot',replace(settings,agent=replace(settings.agent,agent_max_plan_revisions=2)))
    graph=build_planning_graph(runtime,checkpointer=saver);graph_runtime.override_graph(graph)
    started=await submit(h,{'input':{'content':[{'type':'text','text':'합계를 내줘'}]}})
    assert started.status_code==202,started.text
    rid=started.json()['id'];await execute();run=await read(h,rid)
    old_plan=run['interrupt'][0]['payload']['plans'][0]
    def body(run,feedback):
        form=run['interrupt'][0]
        return {'run_id':rid,'resume_token':run['resume_token'],'command':{'resume':{
            'action':'answer_clarification' if form['kind']=='planning_question' else 'replan',
            'interaction_id':form['interaction_id'],'revision':form['revision'],'feedback':feedback}}}
    request=body(run,'모든 계획을 거절하고 다른 방법을 원합니다.')
    for patch,status in [({'revision':99},409),({'feedback':' '},422),({'code':SOURCE},422),({'action':'answer_clarification'},422)]:
        bad=deepcopy(request);bad['command']['resume'].update(patch)
        assert (await submit(h,bad)).status_code==status
    assert (await read(h,rid))['resume_token']==run['resume_token']
    assert (await submit(h,request,key='revise-once')).status_code==202
    await execute();question_run=await read(h,rid)
    assert question_run['status']=='waiting_input' and question_run['interrupt'][0]['kind']=='planning_question'
    assert len(calls)==1 and not executor.calls
    assert (await submit(h,request,key='revise-once')).status_code==202
    assert (await submit(h,request,key='stale-form')).status_code==409
    await graph_runtime.shutdown();graph_runtime.start()
    graph_runtime.override_graph(build_planning_graph(runtime,checkpointer=saver))
    assert (await submit(h,body(question_run,'2로 나눠주세요'),key='answer-once')).status_code==202
    await execute();revised=await read(h,rid)
    assert revised['id']==rid and revised['status']=='waiting_input'
    plan=revised['interrupt'][0]['payload']['plans'][0]
    assert plan['execution_kind']=='free_code' and not plan['workflow_eligible']
    assert (await submit(h,body(revised,'다시 해주세요'))).status_code==422
    assert (await read(h,rid))['resume_token']==revised['resume_token']
    old={'run_id':rid,'resume_token':revised['resume_token'],'command':{'resume':{
        'action':'approve_plan','plan_id':old_plan['plan_id'],'plan_revision':1}}}
    assert (await submit(h,old)).status_code==422
    approval={'run_id':rid,'resume_token':revised['resume_token'],'command':{'resume':{
        'action':'approve_plan','plan_id':plan['plan_id'],'plan_revision':1}}}
    assert (await submit(h,approval,key='approve-revised')).status_code==202
    await execute();final=await read(h,rid)
    assert final['status']=='success' and final['result']['final_response']['approved_plan']['execution_kind']=='free_code'
    assert (await submit(h,approval,key='approve-revised')).status_code==202
    stream=await h.client.get(h.path+'/'+rid+'/stream',headers={'X-User-Id':h.user['user_id']})
    assert SOURCE not in stream.text and 'def revision_transform' not in stream.text and 'code_sha256' not in stream.text
    packets=[json.loads(line[6:]) for line in stream.text.splitlines() if line.startswith('data: ')]
    questions=[p for p in packets if p.get('type') in {'interaction.opened','interaction.updated'} and p.get('data',{}).get('kind')=='planning_question']
    assert len(questions)==1;ClarificationEvent.model_validate(questions[0])
    transitions=[p for p in packets if p.get('type')=='interaction.resolved' and p.get('data',{}).get('resolution') in {'replanning','answered'}]
    assert len(transitions)==2
    for packet in transitions:PlanningTransitionEvent.model_validate(packet)
    assert len(calls)==2 and not executor.calls
