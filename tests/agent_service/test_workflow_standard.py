"""Public standard, editing, order barriers and real registered function execution."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from jsonschema_rs import Draft202012Validator
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from tests.agent_service.asset_fixtures import assets
from tests.agent_service.test_agentic_execution import LocalExecutor, Bindings
from dtest.agent_service.agents.analysis.planning.runtime import PlanningRuntime
from dtest.agent_service.agents.analysis.planning.graph import build_planning_graph
from dtest.agent_service.agents.analysis.agent_builders.execution_review.agent import ReviewResponse
from dtest.application.runs.graph_invocation import GraphInvocation
from dtest.contracts.events import EventContext, ExecutorEvent
from dtest.contracts.plan_review import new_review, patch_review, freeze_approval, PlanReviewError
from dtest.contracts.tool_outputs import output_bindings
from dtest.contracts.workflow_standard import normalize, classification, standard_schema, WorkflowStandardError
from dtest.settings.loader import load_settings
from dtest.contracts.user_resume import resume_identity, resume_envelope
from dtest.agent_service.agents.analysis.execution.compiler import ready_batch, compile_steps, validate_decisions

POLICY={'allowed_modes':['SINGLE','MULTI'],'repair_level_limit':4,'max_repair_attempts_limit':3}
CONTEXT={'user_id':'u','project_id':'p','session_id':'s','public_run_id':'r'}

def public_document(case, *, adaptive=False, conditional=False):
    decoder={'id':'decode','tool':case['reader'],'arguments':{case['reader_arg']:{'source':'input','name':'payload'}}}
    calculator={'id':'calculate','tool':case['processor'],'arguments':{case['object_arg']:{'source':'tool_output','call_id':'decode','output':case['selector']}}}
    body={'name':'Independent standard example','user_request':'Calculate the approved total',
        'inputs':{'payload':{'value_schema':{'type':'string'}}},
        'steps':[{'id':'operations','skill':case['skill'],'tools':[decoder,calculator]}],
        'expected_outputs':[{'id':'report','kind':'report','description':'Explain actual results','required':True,
            'format':'markdown','source':{'source':'agent_report','evidence_calls':['calculate']}}]}
    if adaptive:
        calculator['arguments'][case['option']]={'source':'agent_decision','decision_id':'scale'}
        body['decisions']=[{'id':'scale','after_calls':['decode'],'instruction':'Choose an allowed multiplier from the observed values.',
            'output_schema':{'type':'integer','enum':[2,3]}}]
    if conditional:
        optional=deepcopy(calculator);optional['id']='optional'
        optional['arguments'][case['option']]={'source':'literal','value':2}
        optional['when']={'op':'eq','left':{'source':'input','name':'include_optional'},'right':{'source':'literal','value':True}}
        tail=deepcopy(calculator);tail['id']='tail'
        body['inputs']['include_optional']={'value_schema':{'type':'boolean'},'default':False}
        body['steps'][0]['tools'] += [optional,tail]
        body['expected_outputs'][0]['source']['evidence_calls'].append('tail')
    return {'workflow_version':'2.0','workflow':body}

@pytest.fixture
def pool(tmp_path):
    return assets(tmp_path/'assets','inventory')

def approved(doc,catalog,values=None,excluded=()):
    review=new_review(doc,values or {'payload':'[2,4,6]'},catalog.metadata,POLICY)
    review=patch_review(review,{'action':'approve_plan','plan_id':review['plan_id'],'plan_revision':review['plan_revision'],
        'excluded_step_ids':list(excluded)},datasets={},context=CONTEXT)
    return freeze_approval(review,catalog.sources,catalog.skill_sources,CONTEXT,catalog.revision)

@pytest.mark.parametrize('name',['inventory','billing'])
def test_schema_normalization_named_outputs_and_no_mutation(tmp_path,name):
    catalog,case=assets(tmp_path/name,name);doc=public_document(case);before=deepcopy(doc)
    assert Draft202012Validator(standard_schema()).is_valid(doc)
    plan=normalize(doc,catalog.metadata)
    assert plan['steps'][1]['arguments'][case['object_arg']]['selector']==[case['selector']]
    assert plan['ordered_call_ids']==['decode','calculate'] and classification(plan)=='static'
    assert doc==before
    assert normalize(doc,catalog.metadata)==plan

@pytest.mark.parametrize('mutation,match',[
    ('duplicate','Duplicate Tool call'),('future','precede'),('output','Unknown registered output'),
    ('tool','unregistered tool'),('skill','unregistered skill'),('argument','unknown tool arguments'),
    ('required','missing tool arguments'),('input','unknown input'),('version','2.0'),
    ('condition_missing','Additional properties'),('decision_evidence','precede')])
def test_semantic_failures(pool,mutation,match):
    catalog,case=pool;doc=public_document(case,adaptive=mutation=='decision_evidence')
    calls=doc['workflow']['steps'][0]['tools']
    if mutation=='duplicate':calls[1]['id']='decode'
    if mutation=='future':calls[0]['arguments'][case['reader_arg']]={'source':'tool_output','call_id':'calculate','output':'result'}
    if mutation=='output':calls[1]['arguments'][case['object_arg']]['output']='not_registered'
    if mutation=='tool':calls[1]['tool']='not_registered'
    if mutation=='skill':doc['workflow']['steps'][0]['skill']='not_registered'
    if mutation=='argument':calls[1]['arguments']['not_registered']={'source':'literal','value':1}
    if mutation=='required':calls[1]['arguments']={}
    if mutation=='input':calls[0]['arguments'][case['reader_arg']]['name']='missing'
    if mutation=='version':doc['workflow_version']='1.0'
    if mutation=='condition_missing':calls[1]['execution']='conditional'
    if mutation=='decision_evidence':doc['workflow']['decisions'][0]['after_calls']=['calculate']
    with pytest.raises(WorkflowStandardError,match=match):normalize(doc,catalog.metadata)

def test_literal_nested_source_is_data_not_a_reference(pool):
    catalog,case=pool;doc=public_document(case)
    calls=doc['workflow']['steps'][0]['tools']
    catalog=deepcopy(catalog.metadata)
    catalog['tools'][case['reader']].pop('parameter_bindings')
    calls[0]['arguments'][case['reader_arg']]={'source':'literal','value':{'source':'tool_output','call_id':'missing'}}
    plan=normalize(doc,catalog)
    assert plan['steps'][0]['arguments'][case['reader_arg']]['value']['call_id']=='missing'

def test_agent_parameter_decision_without_conditional_is_adaptive(pool):
    catalog,case=pool;snapshot=approved(public_document(case,adaptive=True),catalog)
    batch,_=ready_batch(snapshot,[],[],{})
    assert [s['id'] for s in batch]==['decode']
    assert classification(snapshot['document'])=='adaptive'
    batch,_=ready_batch(snapshot,['decode'],[],{})
    assert not batch
    with pytest.raises(PlanReviewError):validate_decisions(snapshot,{'scale':9},['decode'])
    assert validate_decisions(snapshot,{'scale':3},['decode'])=={'scale':3}
    batch,_=ready_batch(snapshot,['decode'],[],{'scale':3})
    assert batch[0]['id']=='calculate'

def test_false_conditional_does_not_skip_independent_tail(pool):
    catalog,case=pool;snapshot=approved(public_document(case,conditional=True),catalog)
    batch,skipped=ready_batch(snapshot,[],[],{})
    assert [s['id'] for s in batch]==['decode','calculate','tail'] and skipped==['optional']

def test_order_barrier_holds_tail_until_agent_decision_is_resolved(pool):
    catalog,case=pool;doc=public_document(case,conditional=True)
    calls=doc['workflow']['steps'][0]['tools']
    calls[2]['when']['left']={'source':'agent_decision','decision_id':'continue'}
    doc['workflow']['decisions']=[{'id':'continue','after_calls':['calculate'],'instruction':'Consider optional work.',
        'output_schema':{'type':'boolean'}}]
    snapshot=approved(doc,catalog)
    batch,_=ready_batch(snapshot,[],[],{})
    assert [s['id'] for s in batch]==['decode','calculate']
    batch,skipped=ready_batch(snapshot,['decode','calculate'],[],{'continue':False})
    assert [s['id'] for s in batch]==['tail'] and skipped==['optional']

def test_exclusion_and_parameter_edit_preserve_ids_and_original(pool):
    catalog,case=pool;doc=public_document(case,conditional=True);original=deepcopy(doc)
    review=new_review(doc,{'payload':'[3,5]'},catalog.metadata,POLICY)
    edited=patch_review(review,{'action':'approve_plan','plan_id':review['plan_id'],'plan_revision':1,
        'excluded_step_ids':['optional'],'step_changes':[{'step_id':'calculate','parameter':case['option'],'value':3}]},datasets={},context=CONTEXT)
    snap=freeze_approval(edited,catalog.sources,catalog.skill_sources,CONTEXT,catalog.revision)
    assert [s['id'] for s in snap['steps']]==['decode','calculate','tail']
    batch,_=ready_batch(snap,[],[],{})
    assert [s['id'] for s in batch]==['decode','calculate','tail'] and doc==original
    with pytest.raises(PlanReviewError,match='dependency'):
        patch_review(review,{'action':'edit_plan','plan_id':review['plan_id'],'plan_revision':1,'excluded_step_ids':['decode']},datasets={},context=CONTEXT)

def test_conditional_output_needs_identical_guard(pool):
    catalog,case=pool;doc=public_document(case,conditional=True)
    calls=doc['workflow']['steps'][0]['tools']
    calls[3]['arguments'][case['object_arg']]={'source':'tool_output','call_id':'optional','output':case['result']}
    with pytest.raises(WorkflowStandardError,match='same explicit guard'):normalize(doc,catalog.metadata)
    calls[3]['when']=deepcopy(calls[2]['when'])
    normalize(doc,catalog.metadata)

def test_safe_output_selector_override_and_rejection():
    assert output_bindings({'data':{'selector':[]},'metric':{'selector':['result',0,'value']}})['data']['selector']==[]
    for bad in ['df.query()',[-1],[True],[''],[{}]]:
        with pytest.raises(ValueError):output_bindings({'data':{'selector':bad}})

def test_received_original_bytes_match_manifest():
    root=Path(__file__).resolve().parents[2]/'docs/contracts/workflow-standard/original-1.0'
    manifest=json.loads((root/'SHA256.json').read_text())
    for name,digest in manifest['files'].items():assert hashlib.sha256((root/name).read_bytes()).hexdigest()==digest

@pytest.mark.parametrize('name',['inventory','billing'])
@pytest.mark.asyncio
async def test_public_standard_through_graph_decision_operation_finalize_and_report(tmp_path,monkeypatch,name):
    catalog,case=assets(tmp_path/'assets',name);public=public_document(case,adaptive=True,conditional=True)
    settings=load_settings(config={'MODEL_PROVIDER':'mock','EXECUTOR_SOURCE_TYPE':'INLINE','EXECUTOR_RUNTIME_PROFILE':'default',
        'EXECUTOR_SHARED_RESULT_ROOT':str(tmp_path),'EXECUTOR_BASE_URL':'http://test','EXECUTOR_SUBMIT_ENABLED':True},environ={}).agent
    executor=LocalExecutor(tmp_path);runtime=PlanningRuntime(settings,catalog=catalog,executor=executor,bindings=Bindings())
    async def respond(*args):
        from dtest.agent_service.agents.analysis.agent_builders.conversation.agent import reply_schema
        return reply_schema(catalog,1)(kind='plans',message='Registered plan',plans=[{'definition':normalize(public,catalog.metadata),'input_values':{'payload':'[2,4,6]'}}])
    original_role=runtime.execution_role
    async def role(kind,state,payload):
        if kind=='review':
            return ReviewResponse(choices=[{'decision_id':'scale','value':3,'reason':'Observed values support the approved multiplier.',
                'evidence_steps':['decode']}],needs_user_input=False,message='Selected allowed multiplier.')
        return await original_role(kind,state,payload)
    monkeypatch.setattr(runtime,'respond',respond);monkeypatch.setattr(runtime,'execution_role',role)
    graph=build_planning_graph(runtime,checkpointer=InMemorySaver())
    value={k:str(uuid4()) for k in ('user_id','project_id','session_id','run_id')}
    value.update(user_request='Compute total',model_selection=runtime.models.select().model_dump())
    cfg={'configurable':{'thread_id':value['session_id']}}
    state=await graph.ainvoke(value,cfg,durability='sync');view=state['plan_views'][0]
    assert not executor.calls
    command={'resume':{'action':'approve_plan','plan_id':view['plan_id'],'plan_revision':view['plan_revision']}}
    boundary=state['__interrupt__'][0];identity=resume_identity(str(uuid4()),boundary.id,command)
    state=await graph.ainvoke(Command(resume={boundary.id:resume_envelope(identity,command)}),cfg,durability='sync')
    assert len(executor.calls)==1
    import dtest.application.runs.persistence.graph as persistence
    async def persist(*args,**kwargs):return args[0]
    monkeypatch.setattr(persistence,'persist_graph_state',persist)
    async def deliver(event):
        current=(await graph.aget_state(cfg)).values
        ctx=EventContext(namespace='test',session_id=value['session_id'],task_id=current['task_id'],execution_id=UUID(executor.id),
            command_id=uuid4(),event=ExecutorEvent.model_validate(event))
        await GraphInvocation(graph,model_validator=None).executor_resume(ctx)
        return (await graph.aget_state(cfg)).values
    state=await deliver(executor.events[0]);assert state['execution_decisions']=={'scale':3}
    assert len(executor.calls)==2 and executor.calls[1][0].endswith('/operations')
    state=await deliver(executor.events[1]);assert state['skipped_steps']==['optional']
    assert 'tail' in state['completed_steps'] and executor.calls[-1][0].endswith('/finalize')
    state=await deliver(executor.event('execution.completed',{'status':'SUCCEEDED','error':None}))
    assert state['final_response']['status']=='analysis_completed' and state['report_status']=='ready'
    assert not (await graph.aget_state(cfg)).next


def test_documented_schema_and_examples_match_implementation(tmp_path):
    root=Path(__file__).resolve().parents[2]
    docs=root/'docs/contracts/workflow-standard'
    assert json.loads((docs/'workflow-standard.schema.json').read_text())==standard_schema()
    for mode,name in [('static','inventory'),('adaptive','billing')]:
        catalog,_=assets(tmp_path/name,name)
        source=json.loads((docs/f'workflow_{mode}.example.json').read_text())
        annotated=json.loads('\n'.join(line for line in (docs/f'workflow_{mode}.example.jsonc').read_text().splitlines() if not line.lstrip().startswith('//')))
        assert source==annotated
        assert classification(normalize(source,catalog.metadata))==mode
    def validation_only(value):
        if isinstance(value,dict):return {k:validation_only(v) for k,v in value.items() if k not in {'description','$comment'}}
        if isinstance(value,list):return [validation_only(v) for v in value]
        return value
    internal=json.loads((root/'src/dtest/contracts/resources/workflow-definition.schema.json').read_text())
    mirror=json.loads((root/'docs/design/agentic-workflow-contract/workflow-definition.schema.json').read_text())
    jsonc=json.loads('\n'.join(line for line in (root/'docs/design/agentic-workflow-contract/workflow-definition.schema.jsonc').read_text().splitlines() if not line.lstrip().startswith('//')))
    assert validation_only(internal)==validation_only(mirror)==validation_only(jsonc)


def test_false_flags_null_and_invalid_defaults_are_not_conflated(pool):
    catalog,case=pool;doc=public_document(case)
    doc['workflow']['inputs']['payload'].update(required=False,editable=False,default='[1]')
    field=normalize(doc,catalog.metadata)['inputs']['payload']
    assert field['required'] is False and field['editable'] is False
    doc['workflow']['inputs']['payload']['default']=None
    with pytest.raises(WorkflowStandardError,match='default'):normalize(doc,catalog.metadata)
    doc['workflow']['inputs']['payload']['value_schema']={'type':['string','null']}
    assert normalize(doc,catalog.metadata)['inputs']['payload']['default'] is None


def test_output_override_survives_registry_generation(pool):
    import yaml
    from dtest.agent_service.agents.analysis.planning.catalog import AssetCatalog
    from dtest.agent_service.agents.analysis.workflow.tools.generate_tool_registry import build_registry, write_registry
    catalog,case=pool
    path=catalog.root/'tools/tool_registry.yaml'
    registry=yaml.safe_load(path.read_text())
    registry['tools'][case['reader']]['outputs']={'whole':{'selector':[]}}
    write_registry(registry,path)
    regenerated=build_registry(catalog.root/'tools')
    assert regenerated['tools'][case['reader']]['outputs']=={'whole':{'selector':[]}}
    replacement=AssetCatalog(catalog.root)
    assert replacement.metadata['tools'][case['reader']]['outputs']=={'whole':{'selector':[]}}
    assert replacement.revision!=catalog.revision


def test_arbitrary_return_keys_do_not_break_registered_tool_loading():
    from dtest.contracts.tool_outputs import derived_outputs
    hints={'outputs':{'ROC AUC':{'selector':'["ROC AUC"]'}}}
    assert derived_outputs(hints)=={'result':{'selector':[]}}
    assert output_bindings({'auc':{'selector':['ROC AUC']}})=={'auc':{'selector':['ROC AUC']}}


def test_actual_uppercase_and_underscore_function_arguments_compile_and_execute(pool):
    import re,yaml
    from dtest.agent_service.agents.analysis.planning.catalog import AssetCatalog
    catalog,case=pool;case=deepcopy(case)
    old_object,old_option=case['object_arg'],case['option']
    source=catalog.root/'tools/business_functions.py'
    code=re.sub(r'\b'+old_object+r'\b','X',source.read_text())
    source.write_text(re.sub(r'\b'+old_option+r'\b','_Factor',code))
    path=catalog.root/'tools/tool_registry.yaml';registry=yaml.safe_load(path.read_text())
    tool=registry['tools'][case['processor']]
    tool['parameter_bindings']['X']=tool['parameter_bindings'].pop(old_object)
    tool['parameter_controls']['_Factor']=tool['parameter_controls'].pop(old_option)
    path.write_text(yaml.safe_dump(registry))
    case.update(object_arg='X',option='_Factor');catalog=AssetCatalog(catalog.root)
    doc=public_document(case)
    doc['workflow']['steps'][0]['tools'][1]['arguments']['_Factor']={'source':'literal','value':3}
    snapshot=approved(doc,catalog)
    batch,_=ready_batch(snapshot,[],[],{})
    operations=compile_steps(snapshot,batch,{},0)
    namespace={}
    for operation in operations:exec(operation['payload']['source']['content'],namespace)
    from dtest.agent_service.agents.analysis.execution.compiler import variable
    result=namespace[variable('calculate')][case['result']]
    assert (result['total'] if case['nested'] else result)==36
