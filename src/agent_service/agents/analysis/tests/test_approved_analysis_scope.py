"""Final approved bindings must survive reports, budgets and later turns."""
from copy import deepcopy
import json
from uuid import uuid4

import pytest

from agent_service.runtime.analysis_scope import execution_scope
from agent_service.runtime.session_analysis import bounded_analysis
from agent_service.agents.analysis.tests.test_session_analysis_context import saved_record


def sample():
    steps = [
        {'id':'load','tool_id':'data_load','arguments':{'path':{'source':'workflow_input','name':'dataset'}}},
        {'id':'stats','tool_id':'compute_statistics','arguments':{
            'columns':{'source':'workflow_input','name':'columns'},
            'nullable':{'source':'literal','value':None},
            'optional':{'source':'workflow_input','name':'not_supplied'},
            'data':{'source':'step_output','step_id':'load','selector':[]},
            'method':{'source':'agent_decision','decision_id':'method'},
            'pending':{'source':'agent_decision','decision_id':'pending'},
            'outdir':{'source':'system_context','key':'dataset_output_dir'},
        }},
        {'id':'outliers','tool_id':'detect_outliers','arguments':{}},
        {'id':'failed','tool_id':'failed_tool','arguments':{}},
        {'id':'later','tool_id':'later_tool','arguments':{}},
    ]
    snapshot = {'plan_id':'p','plan_revision':2,'document':{'goal':'max_val과 x 분석','steps':steps},
        'steps':steps[:-3]+steps[-2:], 'excluded_step_ids':['outliers'],
        'input_values':{'columns':['max_val'],'dataset':'d'},
        'dataset_bindings':{'dataset':{'dataset_id':'d','title':'공개 데이터','runtime_path':'/private/data.parquet'}},
        'context':{'dataset_output_dir':'/private/output'},'tool_sources':{'secret':'RAW_SOURCE'}}
    state = {'execution_decisions':{'method':'iqr'},'approved_snapshot':deepcopy(snapshot)}
    observations = [{'step_id':'load','status':'SUCCEEDED'}, {'step_id':'stats','status':'FAILED'},
                    {'step_id':'stats','status':'SUCCEEDED'}, {'step_id':'failed','status':'FAILED'}]
    return state,snapshot,observations


def test_scope_uses_exact_final_bindings_and_separates_user_exclusion_from_outcomes():
    state,snapshot,observations=sample()
    scope=execution_scope(state,snapshot,observations,[])
    assert [s['status'] for s in scope['steps']]==['SUCCEEDED','SUCCEEDED','FAILED','NOT_EXECUTED']
    args=scope['steps'][1]['arguments']
    assert args['columns']['value']==['max_val']
    assert args['nullable']['value'] is None and args['optional']=={'source':'workflow_input','input_name':'not_supplied','has_value':False}
    assert args['method']['value']=='iqr' and not args['pending']['has_value']
    assert args['data']=={'source':'step_output','step_id':'load','selector':[]}
    assert scope['excluded_steps'][0]['status']=='EXCLUDED_BY_USER'
    text=json.dumps(scope)
    assert '/private' not in text and 'RAW_SOURCE' not in text and 'runtime_path' not in text
    scope['steps'][1]['arguments']['columns']['value'].append('x')
    assert snapshot['input_values']['columns']==['max_val']
    assert execution_scope(state,snapshot,observations,['stats'])['steps'][1]['status']=='SKIPPED'


def test_repaired_effective_scope_preserves_original_exclusions():
    state,snapshot,observations=sample()
    snapshot['document']['steps']=snapshot['steps']
    snapshot['steps'][1]['arguments']['columns']={'source':'literal','value':['corrected']}
    scope=execution_scope(state,snapshot,observations,[])
    assert scope['steps'][1]['arguments']['columns']['value']==['corrected']
    assert scope['excluded_steps'][0]['step_id']=='outliers'
    assert scope['parameters_scope']=='effective_approved_plan_not_attempt_history'


def test_budget_keeps_whole_scope_or_discloses_omission_and_normalizes_legacy_goal():
    state,snapshot,observations=sample()
    payload=saved_record()['payload']
    payload['execution_scope']=execution_scope(state,snapshot,observations,[])
    large=bounded_analysis(payload,16000)
    assert large['requested_goal']==payload['goal'] and 'goal' not in large
    assert large['execution_scope']==payload['execution_scope']
    assert not large['execution_scope_omitted']
    assert bounded_analysis(large,16000)==large
    # A long parameter is never sliced into a new approved value.
    payload['execution_scope']['steps'][1]['arguments']['columns']['value']=['x'*10000]
    small=bounded_analysis(payload,2048)
    assert small['execution_scope'] is None and small['execution_scope_omitted']
    assert len(json.dumps(small,ensure_ascii=False,separators=(',',':')))<=2048
    assert bounded_analysis(small,2048)==small
    assert small['observations'][0]['summary']['mean']==11


@pytest.mark.asyncio
async def test_edited_approval_reaches_report_and_rebuilt_followup_without_extra_execution(tmp_path,monkeypatch):
    from agent_service.agents.analysis.tests.test_agentic_execution import setup
    from agent_service.agents.analysis.planning.runtime import PlanningRuntime
    from agent_service.agents.analysis.planning.graph import build_planning_graph
    from agent_service.agents.analysis.agent_builders.conversation.agent import reply_schema
    calls=[]
    old=PlanningRuntime.execution_role
    async def role(self,name,state,payload):
        if name=='report':calls.append(deepcopy(payload))
        return await old(self,name,state,payload)
    monkeypatch.setattr(PlanningRuntime,'execution_role',role)
    runtime,executor,graph,config,state,deliver=await setup(tmp_path,monkeypatch,approval_changes={
        'step_changes':[{'step_id':'statistics','parameter':'columns','value':['value']}],
        'excluded_step_ids':['outliers']})
    _,state=await deliver(executor.events[0])
    _,state=await deliver(executor.event('execution.completed',{'status':'SUCCEEDED','error':None}))
    assert state['final_response']['status']=='analysis_completed' and len(executor.calls)==2
    scope=calls[0]['execution_scope']
    assert 'goal' not in calls[0] and calls[0]['requested_goal']
    assert next(s for s in scope['steps'] if s['step_id']=='statistics')['arguments']['columns']['value']==['value']
    assert scope['excluded_steps'][0]['step_id']=='outliers'
    assert scope['excluded_steps'][0]['status']=='EXCLUDED_BY_USER'
    assert state['last_analysis_context']['payload']['execution_scope']==scope
    assert all(o['step_id']!='outliers' for o in state['final_response']['observations'])
    contexts=[]
    async def respond(value,context,datasets):
        contexts.append(deepcopy(context.session_analysis_context))
        return reply_schema(runtime.catalog,5)(kind='answer',message='최종 승인한 통계 결과입니다.',plans=[])
    monkeypatch.setattr(runtime,'respond',respond)
    graph=build_planning_graph(runtime,checkpointer=graph.checkpointer)
    following=await graph.ainvoke({**{k:state[k] for k in ('user_id','project_id','session_id','model_selection')},
        'run_id':str(uuid4()),'user_request':'방금 결과 설명'},config,durability='sync')
    assert contexts[0]['payload']['execution_scope']==scope
    assert following['final_response']['status']=='answer' and len(executor.calls)==2
