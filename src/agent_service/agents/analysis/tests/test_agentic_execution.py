"""Run actual registered Tool code in an in-process Executor test double."""
import contextlib
import hashlib
import io
import json
from copy import deepcopy
from datetime import datetime,timezone
from uuid import uuid4,UUID
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from service_settings import load_settings
from service_contracts.user_resume import resume_identity,resume_envelope
from service_contracts.events import EventContext,ExecutorEvent
from agent_service.agents.analysis.planning.runtime import PlanningRuntime
from agent_service.agents.analysis.planning.graph import build_planning_graph
from api_service.agent_worker.langgraph_adapter import LangGraphEventAdapter
from integrations.executor.observations import read_operation_observations


class Bindings:
    def __init__(self):self.rows={}
    async def register(self,**row):
        previous=self.rows.setdefault(str(row['execution_id']),row)
        assert previous==row


class LocalExecutor:
    """Only this explicit test double runs code; production submits HTTP."""
    def __init__(self,root):
        self.root=root;self.calls=[];self.globals={};self.events=[];self.id=str(uuid4());self.version=1
        self.event_sequence=0;self.number=0;self.mode=None

    def event(self,kind,payload):
        self.event_sequence+=1
        event={'event_id':str(uuid4()),'execution_id':self.id,'event_type':kind,'event_sequence':self.event_sequence,
            'schema_version':'1.0','payload':payload,'occurred_at':datetime.now(timezone.utc).isoformat()}
        self.events.append(event)
        return event

    async def request(self,method,url,payload=None):
        assert method=='POST'
        self.calls.append((url,deepcopy(payload)))
        if url.endswith('/finalize'):
            self.version+=1
            return {'status_code':202,'body':{'execution_id':self.id,'operation':None,'state':{'status':'FINALIZING','version':self.version}}}
        if url.endswith('/cancel'):
            return {'status_code':202,'body':{'execution_id':self.id,'state':{'status':'CANCEL_REQUESTED','version':self.version+1}}}
        initial=not url.endswith('/operations')
        if initial:self.mode=payload['lifecycle']['operation_mode']
        spec=payload['operation']['spec'] if initial else payload['spec']
        self.number+=1;operation_id=str(uuid4());steps=[];results=[];failed=False
        for step in spec['steps']:
            sequence=step['sequence'];step_id=str(uuid4());attempt_id=str(uuid4())
            steps.append({'sequence':sequence,'step_id':step_id})
            if failed:
                results.append({'sequence':sequence,'step_id':step_id,'status':'PENDING','result_ref':None,'attempt':None})
                continue
            source=step['payload']['source'];assert source['type']=='INLINE'
            output=io.StringIO();error=None
            try:
                with contextlib.redirect_stdout(output):exec(source['content'],self.globals)
            except Exception as exc:
                failed=True;error=str(exc)
            prefix=f'executions/{self.id}/operations/{operation_id}/steps/{step_id}'
            folder=self.root/prefix;folder.mkdir(parents=True)
            raw=output.getvalue().encode();(folder/'stdout.txt').write_bytes(raw)
            created=datetime.now(timezone.utc).isoformat()
            manifest={'schema_version':'1.0','state':'FAILED' if failed else 'FINALIZED','complete':True,
                'identity':{'execution_id':self.id,'operation_id':operation_id,'step_id':step_id,'sequence':sequence,
                    'execution_attempt_id':attempt_id,'fencing_token':1},
                'source':{'relative_path':prefix+'/source.py','checksum_sha256':hashlib.sha256(source['content'].encode()).hexdigest(),'size_bytes':len(source['content'].encode())},
                'outputs':[{'ordinal':0,'kind':'STREAM','stream_name':'stdout','execution_count':sequence+1,
                    'representations':[{'media_type':'text/plain','encoding':'UTF8','relative_path':prefix+'/stdout.txt',
                        'size_bytes':len(raw),'checksum_sha256':hashlib.sha256(raw).hexdigest(),'complete':True,'truncated_in_preview':False,'metadata':{}}],
                    'metadata':{},'created_at':created}],
                'output_count':1,'representation_count':1,'total_size_bytes':len(raw),'execution_count':sequence+1,
                'error_message':error,'output_summary':{'output_count':1,'output_types':{'STREAM':1},'stream_names':['stdout'],
                    'mime_types':['text/plain'],'has_image':False,'image_count':0,'has_error':failed},
                'created_at':created,'updated_at':created,'completed_at':created}
            encoded=json.dumps(manifest).encode();(folder/'manifest.json').write_bytes(encoded)
            results.append({'sequence':sequence,'step_id':step_id,'status':'FAILED' if failed else 'SUCCEEDED',
                'attempt':{'id':attempt_id,'number':1,'reason':'INITIAL'},'error':error,
                'result_ref':{'storage':'SHARED_PV','relative_path':prefix+'/manifest.json','size_bytes':len(encoded),
                    'checksum_sha256':hashlib.sha256(encoded).hexdigest(),'complete':True}})
        self.version+=2
        self.event('execution.operation_completed',{'operation':{'id':operation_id,'number':self.number},'step_results':results,
            'status':'FAILED' if failed else 'SUCCEEDED','execution_status':'WAITING_FOR_OPERATION' if self.mode=='MULTI' else 'FINALIZING',
            'error':{'message':'test Step failure'} if failed else None,
            'continuation':{'allowed':True,'expected_version':self.version,'expires_at':'2100-01-01T00:00:00+00:00'} if self.mode=='MULTI' else None})
        return {'status_code':202,'body':{'execution_id':self.id,'operation':{'operation_id':operation_id,'steps':steps},
            'state':{'status':'QUEUED','version':self.version-2}}}


async def setup(tmp_path,monkeypatch,*,single=False,missing=False):
    pd=pytest.importorskip('pandas')
    file=tmp_path/'data.parquet'
    pd.DataFrame({'value':[1.,2.,3.,4.,5.,90.]}).to_parquet(file)
    settings=load_settings(config={'MODEL_PROVIDER':'mock','EXECUTOR_SOURCE_TYPE':'INLINE','EXECUTOR_RUNTIME_PROFILE':'default',
        'EXECUTOR_SHARED_RESULT_ROOT':str(tmp_path),'EXECUTOR_BASE_URL':'http://test','EXECUTOR_SUBMIT_ENABLED':True,
        'ANALYSIS_DATASETS':{'dataset':{'title':'검증 데이터','scope':'GLOBAL','runtime_path':str(tmp_path/'missing.parquet' if missing else file)}}},environ={})
    executor=LocalExecutor(tmp_path);bindings=Bindings()
    runtime=PlanningRuntime(settings.agent,executor=executor,bindings=bindings)
    if single:
        from importlib.resources import files
        from agent_service.agents.analysis.agent_builders.conversation.agent import reply_schema
        document=json.loads(files('agent_service.agents.analysis.planning').joinpath('fixtures/quality-review.json').read_text())
        document['decisions']=[];document['execution']['mode']='SINGLE'
        document['steps'][-1]['arguments']['method']={'source':'literal','value':'iqr'};document['steps'][-1].pop('when')
        for item in document['expected_outputs']:item.pop('when',None)
        async def respond(*args):return reply_schema(runtime.catalog,5)(kind='plans',message='Test SINGLE',plans=[{'definition':document,'input_values':{'dataset':'dataset'}}])
        monkeypatch.setattr(runtime,'respond',respond)
    graph=build_planning_graph(runtime,checkpointer=InMemorySaver())
    value={k:str(uuid4()) for k in ('user_id','project_id','session_id','run_id')}
    value.update(user_request='품질 분석',model_selection=runtime.models.select().model_dump(),initial_request_identity={'command_id':value['run_id']})
    config={'configurable':{'thread_id':value['session_id']}}
    state=await graph.ainvoke(value,config,durability='sync')
    view=state['plan_views'][0];command={'resume':{'action':'approve_plan','plan_id':view['plan_id'],'plan_revision':view['plan_revision']}}
    boundary=state['__interrupt__'][0];identity=resume_identity(str(uuid4()),boundary.id,command)
    state=await graph.ainvoke(Command(resume={boundary.id:resume_envelope(identity,command)}),config,durability='sync')
    import api_service.services.graph_crud_persistence as persistence
    async def persist(*args,**kwargs):return args[0]
    monkeypatch.setattr(persistence,'persist_graph_state',persist)
    adapter=LangGraphEventAdapter(graph)
    async def deliver(event):
        current=(await graph.aget_state(config)).values
        ctx=EventContext(namespace='test',session_id=value['session_id'],task_id=current['task_id'],execution_id=UUID(executor.id),
            command_id=uuid4(),event=ExecutorEvent.model_validate(event))
        await adapter(ctx)
        return ctx,(await graph.aget_state(config)).values
    return runtime,executor,graph,config,state,deliver


@pytest.mark.asyncio
async def test_multi_real_tools_decision_operation_sequence_finalize_terminal_and_receipt_replay(tmp_path,monkeypatch):
    runtime,executor,graph,config,state,deliver=await setup(tmp_path,monkeypatch)
    assert state['__interrupt__'][0].value['kind']=='EXECUTOR_EVENT' and len(executor.calls)==1
    assert not state['final_response']
    _,state=await deliver(executor.events[0])
    assert state['completed_steps']==['load','profile','statistics']
    assert state['observations'][0]['summary']['shape']==[6,1]
    assert executor.calls[1][1]['spec']['steps'][0]['sequence']==3
    assert executor.calls[1][1]['expected_version']==3
    _,state=await deliver(executor.events[1])
    assert executor.calls[-1][0].endswith('/finalize') and not state['final_response']
    assert (await graph.aget_state(config)).next==('execution_wait',)
    terminal=executor.event('execution.completed',{'status':'SUCCEEDED','error':None})
    ctx,state=await deliver(terminal)
    assert not (await graph.aget_state(config)).next
    assert state['final_response']['status']=='analysis_completed' and state['report_status']=='ready'
    assert state['observations'][-1]['summary']['items']['outlier_indices']['items']==[5]
    assert len(executor.calls)==3
    await LangGraphEventAdapter(graph)(ctx)
    assert len(executor.calls)==3
    assert state['execution_decisions']['inspect_outliers'] is True
    assert state['final_response']['report']['artifact_registration']=='deferred'


@pytest.mark.asyncio
async def test_false_condition_skips_tool_and_finalizes_without_fabricated_result(tmp_path,monkeypatch):
    runtime,executor,graph,config,state,deliver=await setup(tmp_path,monkeypatch)
    old=runtime.execution_role
    async def role(name,*args):
        result=await old(name,*args)
        if name=='review':
            for choice in result.choices:
                if choice.decision_id=='inspect_outliers':choice.value=False
        return result
    monkeypatch.setattr(runtime,'execution_role',role)
    _,state=await deliver(executor.events[0])
    assert state['skipped_steps']==['outliers'] and executor.calls[-1][0].endswith('/finalize')
    assert len(executor.calls)==2


@pytest.mark.asyncio
async def test_single_has_no_continuation_or_finalize_and_waits_for_terminal(tmp_path,monkeypatch):
    runtime,executor,graph,config,state,deliver=await setup(tmp_path,monkeypatch,single=True)
    assert executor.calls[0][1]['lifecycle']=={'operation_mode':'SINGLE'}
    _,state=await deliver(executor.events[0])
    assert state['executor_wait_phase']=='execution_completed' and len(executor.calls)==1
    _,state=await deliver(executor.event('execution.completed',{'status':'SUCCEEDED','error':None}))
    assert state['final_response']['status']=='analysis_completed'


@pytest.mark.asyncio
async def test_single_tool_error_preserved_without_repair_or_finalize(tmp_path,monkeypatch):
    runtime,executor,graph,config,state,deliver=await setup(tmp_path,monkeypatch,single=True,missing=True)
    _,state=await deliver(executor.events[0])
    assert state['observations'][0]['status']=='FAILED'
    _,state=await deliver(executor.event('execution.completed',{'status':'FAILED','error':{'message':'File missing'}}))
    assert state['final_response']['status']=='analysis_failed' and len(executor.calls)==1


@pytest.mark.asyncio
async def test_terminal_timeout_can_close_user_decision_wait(tmp_path,monkeypatch):
    runtime,executor,graph,config,state,deliver=await setup(tmp_path,monkeypatch)
    old=runtime.execution_role
    async def role(name,*args):
        result=await old(name,*args)
        if name=='review':result.needs_user_input=True
        return result
    monkeypatch.setattr(runtime,'execution_role',role)
    _,state=await deliver(executor.events[0])
    snapshot=await graph.aget_state(config)
    assert snapshot.tasks[0].interrupts[0].value['kind']=='decision_review'
    _,state=await deliver(executor.event('execution.completed',{'status':'FAILED','error':{'code':'WAIT_TIMEOUT'}}))
    assert not (await graph.aget_state(config)).next
    assert state['final_response']['status']=='analysis_failed' and len(executor.calls)==1


@pytest.mark.asyncio
async def test_manifest_checksum_tampering_is_rejected(tmp_path,monkeypatch):
    runtime,executor,graph,config,state,deliver=await setup(tmp_path,monkeypatch)
    event=executor.events[0];ref=event['payload']['step_results'][0]['result_ref']
    (tmp_path/ref['relative_path']).write_text('{}')
    with pytest.raises(ValueError,match='size mismatch'):
        read_operation_observations(runtime.settings,event,state['submitted_steps'])


@pytest.mark.asyncio
async def test_user_decision_validation_and_resume_preserve_execution_identity(tmp_path,monkeypatch):
    from service_contracts.execution_review import validate_decision_action
    runtime,executor,graph,config,state,deliver=await setup(tmp_path,monkeypatch)
    old=runtime.execution_role
    async def role(name,*args):
        result=await old(name,*args)
        if name=='review':result.needs_user_input=True
        return result
    monkeypatch.setattr(runtime,'execution_role',role)
    _,state=await deliver(executor.events[0])
    review=state['decision_review']
    values={f['decision_id']:f['value'] for f in review['payload']['decisions']}
    action={'action':'approve_decisions','interaction_id':review['interaction_id'],'revision':1,'values':values}
    with pytest.raises(ValueError,match='schema'):
        validate_decision_action(review,{**action,'values':{**values,'inspect_outliers':'yes'}})
    assert validate_decision_action(review,action)==values
    boundary=(await graph.aget_state(config)).tasks[0].interrupts[0]
    identity=resume_identity(str(uuid4()),boundary.id,{'resume':action})
    state=await graph.ainvoke(Command(resume={boundary.id:resume_envelope(identity,{'resume':action})}),config,durability='sync')
    assert state['execution_id']==executor.id and len(executor.calls)==2
    assert state['public_run_id']==state['approved_snapshot']['run_id']
    assert state['executor_operation_number']==2
    _,state=await deliver(executor.events[1])
    assert executor.calls[-1][0].endswith('/finalize')


def test_path_sources_are_immutable_and_confined_to_shared_root(tmp_path):
    from agent_service.agents.analysis.execution.compiler import materialize_steps
    step={'sequence':0,'payload':{'source':{'type':'INLINE','content':'print(1)\n'}}}
    first=materialize_steps(tmp_path,[step])
    assert first==materialize_steps(tmp_path,[step])
    source=first[0]['payload']['source'];path=tmp_path/source['path']
    assert hashlib.sha256(path.read_bytes()).hexdigest()==source['sha256']
    path.write_text('changed')
    with pytest.raises(ValueError,match='changed'):
        materialize_steps(tmp_path,[step])
    assert step['payload']['source']['type']=='INLINE'


def test_report_tables_copy_verified_numbers_without_model_recalculation():
    from agent_service.agents.analysis.execution.report import render_evidence_markdown
    observations=[{'step_id':'statistics','tool_id':'compute_statistics','status':'SUCCEEDED',
        'summary':{'type':'dict','items':{'y':{'type':'dict','items':{'75%':38.0},'truncated':False},
            'missing_rate':.0045,'unsafe_label':'<script>|'},'truncated':False}}]
    rendered=render_evidence_markdown(observations)
    assert '| y.75% | 38.0 |' in rendered
    assert '| missing_rate | 0.0045 |' in rendered and '450' not in rendered
    assert '<script>' not in rendered and '&#124;' in rendered


@pytest.mark.asyncio
async def test_invalid_model_report_falls_back_to_verified_facts_and_finishes(tmp_path,monkeypatch):
    from agent_service.middleware.prompt_json import StructuredResponseError
    runtime,executor,graph,config,state,deliver=await setup(tmp_path,monkeypatch)
    _,state=await deliver(executor.events[0])
    _,state=await deliver(executor.events[1])
    original=runtime.execution_role
    async def role(name,*args):
        if name=='report':raise StructuredResponseError('Unusable model interpretation')
        return await original(name,*args)
    monkeypatch.setattr(runtime,'execution_role',role)
    _,state=await deliver(executor.event('execution.completed',{'status':'SUCCEEDED','error':None}))
    assert not (await graph.aget_state(config)).next
    assert state['final_response']['status']=='analysis_completed'
    assert state['final_response']['report']['status']=='evidence_only'
    assert '검증하지 못해' in state['final_response']['report']['content']
