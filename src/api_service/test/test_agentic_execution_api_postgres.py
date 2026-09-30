"""Real API/DB projection and decision admission; explicit local Executor double."""
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver

import service_settings
from api_service.test.test_planning_api_postgres import planning, test_config, submit, execute, read
from api_service.services.agent_graph_service import runtime as graph_runtime
from api_service.services.executor_completion import synchronize_executor_completion
from api_service.agent_worker.langgraph_adapter import LangGraphEventAdapter
from service_contracts.events import EventContext, ExecutorEvent
from service_contracts.plan_interaction import DecisionInteractionEvent
from agent_service.agents.analysis.planning.graph import build_planning_graph
from agent_service.agents.analysis.planning.runtime import PlanningRuntime
from agent_service.agents.analysis.tests.test_agentic_execution import LocalExecutor, Bindings


@pytest.mark.asyncio
async def test_decision_form_validation_does_not_consume_token_and_projects_terminal(planning, tmp_path, monkeypatch):
    h = planning
    import api_service.services.executor_completion as completion
    monkeypatch.setattr(completion, "get_session_factory", lambda: h.factory)
    pd = pytest.importorskip('pandas')
    path = tmp_path/'data.parquet'
    pd.DataFrame({'value':[1.,2.,3.,4.,5.,90.]}).to_parquet(path)
    service = service_settings.get_settings()
    agent = replace(service.agent, executor_submit_enabled=True, executor_source_type='INLINE',
        executor_shared_result_root=tmp_path,
        analysis_datasets={'default-nce':{'title':'Test','scope':'GLOBAL','runtime_path':str(path)}})
    monkeypatch.setattr(service_settings, '_snapshot', replace(service, agent=agent))
    executor = LocalExecutor(tmp_path)
    runtime = PlanningRuntime(agent, executor=executor, bindings=Bindings())
    original = runtime.execution_role
    async def role(name, *args):
        response = await original(name, *args)
        if name=='review':response.needs_user_input=True
        return response
    monkeypatch.setattr(runtime, 'execution_role', role)
    graph = build_planning_graph(runtime, checkpointer=InMemorySaver())
    graph_runtime.override_graph(graph)
    response = await submit(h, {'input':{'content':[{'type':'text','text':'품질 분석'}]}})
    rid = response.json()['id']
    await execute()
    run = await read(h, rid)
    plan = run['interrupt'][0]['payload']['plans'][0]
    approval = {'run_id':rid,'resume_token':run['resume_token'],
        'command':{'resume':{'action':'approve_plan','plan_id':plan['plan_id'],'plan_revision':1}}}
    assert (await submit(h, approval)).status_code==202
    await execute()
    run = await read(h, rid)
    assert run['status']=='waiting_executor' and run['resume_token'] is None

    async def deliver(event):
        state=(await graph.aget_state({'configurable':{'thread_id':h.session_id}})).values
        context=EventContext(namespace='test',session_id=h.session_id,task_id=state['task_id'],
            execution_id=UUID(executor.id),command_id=uuid4(),event=ExecutorEvent.model_validate(event))
        await LangGraphEventAdapter(graph)(context)
        await synchronize_executor_completion(context,graph)

    await deliver(executor.events[0])
    run = await read(h, rid)
    assert run['status']=='waiting_input'
    token = run['resume_token']; form = run['interrupt'][0]
    values={field['decision_id']:field['value'] for field in form['payload']['decisions']}
    action={'action':'approve_decisions','interaction_id':form['interaction_id'],'revision':form['revision'],'values':values}
    body={'run_id':rid,'resume_token':token,'command':{'resume':action}}
    invalid={**body,'command':{'resume':{**action,'values':{**values,'inspect_outliers':'yes'}}}}
    assert (await submit(h,invalid)).status_code==422
    stale={**body,'command':{'resume':{**action,'revision':action['revision']+1}}}
    assert (await submit(h,stale)).status_code==409
    assert (await read(h,rid))['resume_token']==token
    assert len(executor.calls)==1
    assert (await submit(h,body)).status_code==202
    await execute()
    assert len(executor.calls)==2
    await deliver(executor.events[1])
    assert executor.calls[-1][0].endswith('/finalize')
    await deliver(executor.event('execution.completed',{'status':'SUCCEEDED','error':None}))
    final=await read(h,rid)
    assert final['id']==rid and final['status']=='success'
    assert final['result']['final_response']['status']=='analysis_completed'
    stream=await h.client.get(h.path+'/'+rid+'/stream',headers={'X-User-Id':h.user['user_id']})
    import json
    events=[json.loads(line[6:]) for line in stream.text.splitlines() if line.startswith('data: ')]
    decisions=[event for event in events if event.get('type')=='interaction.opened' and event.get('data',{}).get('kind')=='decision_review']
    assert len(decisions)==1
    DecisionInteractionEvent.model_validate(decisions[0])
    previous_task_id=(await graph.aget_state({'configurable':{'thread_id':h.session_id}})).values['task_id']
    response=await submit(h,{'input':{'content':[{'type':'text','text':'새 분석 요청'}]}})
    assert response.status_code==202
    await execute()
    new_run=await read(h,response.json()['id'])
    assert new_run['status']=='waiting_input' and new_run['id']!=rid
    assert (await graph.aget_state({'configurable':{'thread_id':h.session_id}})).values['task_id']!=previous_task_id
