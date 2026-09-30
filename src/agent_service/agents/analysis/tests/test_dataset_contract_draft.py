"""Offline acceptance boundaries for 042, not an Executor/registration integration test."""
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from jsonschema_rs import Draft202012Validator
from service_contracts.dataset_registry_draft import (
    AgentAnnotation, ContractExamples, DataScope, DatasetCandidate, DatasetRecord,
    DatasetRef, DatasetRegistration, FileEvidence, ParquetInspection, RequestContext,
    StoragePolicy, accessible, data_directory, owner_for, public_dataset,
    register_candidate, resolve_binding, selection_id, parse_selection_id,
)

ROOT = Path(__file__).resolve().parents[5] / 'docs/design/dataset-registry-contract'


def fixture():
    return ContractExamples.model_validate_json((ROOT/'examples/project-reuse.json').read_text())


@pytest.mark.parametrize('scope',list(DataScope))
def test_scope_visible_only_to_its_owner_and_allowed_context(scope):
    data=fixture();c=data.context;owner=owner_for(c,scope)
    same_user_new_project=c.model_copy(update={'project_id':uuid4(),'session_id':uuid4()})
    same_project_new_session=c.model_copy(update={'session_id':uuid4()})
    other_user=c.model_copy(update={'user_id':uuid4()})
    assert accessible(owner,c)
    assert not accessible(owner,other_user)
    assert accessible(owner,same_user_new_project)==(scope==DataScope.USER)
    assert accessible(owner,same_project_new_session)==(scope!=DataScope.SESSION)
    root=data_directory(owner)
    assert root.startswith('users/'+str(c.user_id)) and root.endswith('/datasets')
    assert ('projects/' in root)==(scope!=DataScope.USER)
    assert ('sessions/' in root)==(scope==DataScope.SESSION)


@pytest.mark.parametrize('scope',list(DataScope))
def test_offline_register_then_resolve_after_kernel_end_in_new_allowed_session(scope):
    data=fixture();candidate=data.candidate.model_dump(mode='json')
    owner=owner_for(data.context,scope)
    candidate['owner']=owner.model_dump(mode='json')
    candidate['file']['relative_path']=data_directory(owner)+'/outputs/run-demo/data.parquet'
    candidate=DatasetCandidate.model_validate(candidate)
    request=data.registration.model_copy(update={'scope':scope})
    record=register_candidate(request,candidate,execution_id=candidate.producer.execution_id,
        ref=data.record.ref,context=data.context,approved_parent_refs=[])
    target=data.context if scope==DataScope.SESSION else data.context.model_copy(update={'session_id':uuid4()})
    # No kernel variable or live Execution status is required by this contract.
    binding=resolve_binding(record,record.ref,target,data.storage,current_file=candidate.file)
    assert binding.runtime_path.endswith('/data.parquet') and binding.ref==record.ref
    assert record.candidate.producer.step_status=='SUCCEEDED'
    assert record.candidate is not candidate and record.annotation is not request.annotation


@pytest.mark.parametrize('patch',[
    {'runtime_path':'/workspace/pv/other-user/private.parquet'},
    {'owner_user_id':'not-owner'}, {'rows':999}, {'columns':['invented']}, {'code':'print(1)'},
])
def test_registration_request_cannot_set_mechanical_facts_or_code(patch):
    value=fixture().registration.model_dump(mode='json');value.update(patch)
    with pytest.raises(ValidationError):DatasetRegistration.model_validate(value)


def test_annotation_is_separate_from_measured_schema_and_raw_minio_scope():
    data=fixture()
    with pytest.raises(ValidationError):AgentAnnotation(title='Title',rows=123)
    with pytest.raises(ValidationError):DatasetRegistration.model_validate({**data.registration.model_dump(mode='json'),'scope':'GLOBAL'})
    with pytest.raises(ValidationError):AgentAnnotation(title=' ')
    with pytest.raises(ValidationError):AgentAnnotation(title='Title',notes=['x'*1001])
    with pytest.raises(ValidationError):DatasetRef(dataset_id=uuid4(),version=True)


@pytest.mark.parametrize('mutation', ['wrong_execution','wrong_candidate','changed_file','other_context','broaden_scope','failed_step','writing','unknown_publication','unavailable_schema','foreign_parent','duplicate_parent'])
def test_registration_requires_exact_successful_published_candidate(mutation):
    data=fixture();candidate=data.candidate;request=data.registration;execution=candidate.producer.execution_id
    allowed=[];context=data.context
    if mutation=='wrong_execution':execution=uuid4()
    elif mutation=='wrong_candidate':request=request.model_copy(update={'candidate_id':uuid4()})
    elif mutation=='changed_file':request=request.model_copy(update={'expected_file_revision':'changed'})
    elif mutation=='other_context':context=context.model_copy(update={'session_id':uuid4()})
    elif mutation=='broaden_scope':request=request.model_copy(update={'scope':DataScope.USER})
    elif mutation=='failed_step':candidate=candidate.model_copy(update={'producer':candidate.producer.model_copy(update={'step_status':'FAILED'})})
    elif mutation in {'writing','unknown_publication'}:candidate=candidate.model_copy(update={'publication':'WRITING' if mutation=='writing' else 'UNKNOWN'})
    elif mutation=='unavailable_schema':candidate=candidate.model_copy(update={'inspection':ParquetInspection(status='UNAVAILABLE',reason='Footer cannot be inspected')})
    elif mutation=='foreign_parent':request=request.model_copy(update={'annotation':request.annotation.model_copy(update={'declared_parents':[DatasetRef(dataset_id=uuid4(),version=1)]})})
    else:
        parent=DatasetRef(dataset_id=uuid4(),version=1);allowed=[parent]
        request=request.model_copy(update={'annotation':request.annotation.model_copy(update={'declared_parents':[parent,parent]})})
    with pytest.raises(ValueError):register_candidate(request,candidate,execution_id=execution,
        ref=data.record.ref,context=context,approved_parent_refs=allowed)


@pytest.mark.parametrize('mutation',['file_revision','file_size','file_path','namespace','unavailable','wrong_version','other_user','other_project'])
def test_binding_is_pinned_and_new_storage_lookup_cannot_silently_redirect(mutation):
    data=fixture();record=data.record;ref=record.ref;context=data.context;storage=data.storage;current=record.candidate.file
    if mutation=='file_revision':current=current.model_copy(update={'revision':'new-revision'})
    elif mutation=='file_size':current=current.model_copy(update={'size_bytes':999})
    elif mutation=='file_path':current=current.model_copy(update={'relative_path':'other/file.parquet'})
    elif mutation=='namespace':storage=storage.model_copy(update={'namespace':'different-pvc'})
    elif mutation=='unavailable':record=record.model_copy(update={'status':'UNAVAILABLE'})
    elif mutation=='wrong_version':ref=ref.model_copy(update={'version':2})
    elif mutation=='other_user':context=context.model_copy(update={'user_id':uuid4()})
    elif mutation=='other_project':context=context.model_copy(update={'project_id':uuid4()})
    with pytest.raises(ValueError):resolve_binding(record,ref,context,storage,current_file=current)


@pytest.mark.parametrize('path',['../data.parquet','a/../data.parquet','a//data.parquet','a/./data.parquet','/workspace/pv/data.parquet','a\\data.parquet','a/data\n.parquet'])
def test_runtime_relative_path_cannot_escape_or_normalize_away_unsafe_segments(path):
    value=fixture().candidate.file.model_dump(mode='json');value['relative_path']=path
    with pytest.raises(ValidationError):FileEvidence.model_validate(value)


def test_candidate_cannot_claim_someone_elses_root_or_wrong_identity():
    data=fixture();value=data.candidate.model_dump(mode='json')
    value['file']['relative_path']='users/'+str(uuid4())+'/datasets/data.parquet'
    with pytest.raises(ValidationError):DatasetCandidate.model_validate(value)
    value=data.candidate.model_dump(mode='json');value['owner']['project_id']=str(uuid4())
    with pytest.raises(ValidationError):DatasetCandidate.model_validate(value)
    value=data.candidate.model_dump(mode='json');value['producer']['fencing_token']=0
    with pytest.raises(ValidationError):DatasetCandidate.model_validate(value)


def test_large_dataset_metadata_is_bounded_without_reading_entire_data():
    data=fixture();value=data.candidate.model_dump(mode='json')
    value['file']['size_bytes']=200*1024**3
    value['inspection']={'status':'AVAILABLE','rows':10**9,'column_count':1000,
        'columns':[{'index':i,'name':'column-'+str(i),'dtype':'double'} for i in range(200)],'truncated':True}
    candidate=DatasetCandidate.model_validate(value)
    assert candidate.inspection.truncated and len(candidate.inspection.columns)==200
    assert len(candidate.model_dump_json())<25000
    invalid={**value['inspection'],'truncated':False}
    with pytest.raises(ValidationError):ParquetInspection.model_validate(invalid)
    invalid={**value['inspection'],'columns':value['inspection']['columns']+[{'index':200,'name':'extra','dtype':'double'}]}
    with pytest.raises(ValidationError):ParquetInspection.model_validate(invalid)
    with pytest.raises(ValidationError):ParquetInspection(status='UNAVAILABLE',reason='Unknown',rows=1)


def test_public_metadata_excludes_storage_paths_tokens_and_producer_ids():
    data=fixture();view=public_dataset(data.record,data.context);encoded=view.model_dump_json()
    assert view.model_dump(mode='json')==data.public_view.model_dump(mode='json')
    for private in [data.candidate.file.relative_path,data.candidate.file.revision,
        data.storage.runtime_root,data.storage.namespace,str(data.candidate.producer.execution_id)]:
        assert private not in encoded
    assert set(view.model_dump())=={'ref','selection_id','source','title','description','purpose','scope','status','size_bytes','inspection'}
    with pytest.raises(ValueError):public_dataset(data.record,data.context.model_copy(update={'user_id':uuid4()}))


def test_exported_schema_and_fixture_roundtrip_offline():
    schema=json.loads((ROOT/'dataset-contract.schema.json').read_text())
    values=json.loads((ROOT/'examples/project-reuse.json').read_text())
    assert schema['$defs']==ContractExamples.model_json_schema()['$defs']
    Draft202012Validator(schema).validate(values)
    result=ContractExamples.model_validate(values)
    assert result.record.ref.version==1
    malformed=json.loads(json.dumps(values));malformed['registration']['runtime_path']='/workspace/pv/secret'
    with pytest.raises(ValueError):Draft202012Validator(schema).validate(malformed)


@pytest.mark.parametrize('suffix',['latest','0','-1','01','1.0','true','9223372036854775808'])
def test_selection_string_does_not_drop_or_silently_update_version(suffix):
    ref=fixture().record.ref
    with pytest.raises(ValueError):parse_selection_id(f'pvc:{ref.dataset_id}:{suffix}')


def test_versioned_selection_fits_current_workflow_approval_contract():
    from service_contracts.plan_review import new_review,patch_review,freeze_approval
    from agent_service.agents.analysis.planning.catalog import AssetCatalog
    data=fixture();key=selection_id(data.record.ref)
    assert parse_selection_id(key)==data.record.ref
    catalog=AssetCatalog()
    path=Path(__file__).resolve().parents[1]/'planning/fixtures/quality-review.json'
    doc=json.loads(path.read_text())
    context={**data.context.model_dump(mode='json'),'public_run_id':str(uuid4())}
    binding=resolve_binding(data.record,data.record.ref,data.context,data.storage,current_file=data.candidate.file)
    declaration={'title':data.public_view.title,'description':data.public_view.description,
        'runtime_path':binding.runtime_path,'scope':'PROJECT',
        'owner_user_id':str(data.context.user_id),'project_id':str(data.context.project_id)}
    datasets={key:declaration}
    review=new_review(doc,{'dataset':key},catalog.metadata,{'allowed_modes':['MULTI'],'repair_level_limit':4,'max_repair_attempts_limit':3})
    approved=patch_review(review,{'action':'approve_plan','plan_id':review['plan_id'],'plan_revision':1},datasets=datasets,context=context)
    snapshot=freeze_approval(approved,catalog.sources,catalog.skill_sources,context,catalog.revision,datasets)
    assert snapshot['input_values']['dataset']==key
    assert snapshot['dataset_bindings']['dataset']['runtime_path']==binding.runtime_path
    assert snapshot['dataset_bindings']['dataset']['dataset_id']==key
    # This only proves contract compatibility; no dynamic provider has been wired.
