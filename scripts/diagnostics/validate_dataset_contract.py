"""Validate/export 042 Dataset contract examples offline; never calls Executor or files on PVC."""
import argparse
import json
from pathlib import Path
from uuid import UUID

from jsonschema_rs import Draft202012Validator
from service_contracts.dataset_registry_draft import (
    ContractExamples, DatasetCandidate, DatasetRef, DatasetRegistration,
    RequestContext, StoragePolicy, data_directory, owner_for,
    public_dataset, register_candidate, resolve_binding,
)

ROOT = Path(__file__).resolve().parents[2] / 'docs/design/dataset-registry-contract'


def example():
    def uid(value):return UUID(f'00000000-0000-4000-8000-{value:012d}')
    context = RequestContext(user_id=uid(1), project_id=uid(2), session_id=uid(3))
    owner = owner_for(context)
    policy = StoragePolicy(namespace='shared-pvc-main', runtime_root='/workspace/pv')
    candidate = DatasetCandidate(candidate_id=uid(4), owner=owner,
        producer={'execution_id':uid(5),'operation_id':uid(6),'step_id':uid(7),
            'execution_attempt_id':uid(8),'step_attempt_id':uid(9),'sequence':1,'fencing_token':1,
            'context':context,'step_status':'SUCCEEDED'},
        publication='FINALIZED', file={'storage_namespace':policy.namespace,
            'relative_path':data_directory(owner)+'/outputs/run-demo/cleaned.parquet','size_bytes':123456,
            'revision':'demo-stat-footer-revision-1','revision_method':'STAT_AND_FOOTER'},
        inspection={'status':'AVAILABLE','rows':1000,'column_count':2,
            'columns':[{'index':0,'name':'temperature','dtype':'double'}, {'index':1,'name':'target','dtype':'int64'}]})
    request = DatasetRegistration(idempotency_key='demo-dataset-registration-1',candidate_id=candidate.candidate_id,
        expected_file_revision=candidate.file.revision,
        annotation={'title':'이상치를 제외한 온도 분석 데이터','description':'실제 전처리 Step의 관찰을 바탕으로 Agent가 붙인 설명입니다.',
            'purpose':'후속 분석에서 같은 전처리 결과를 재사용합니다.'})
    ref = DatasetRef(dataset_id=uid(10), version=1)
    record = register_candidate(request, candidate, execution_id=candidate.producer.execution_id,
        ref=ref, context=context, approved_parent_refs=[])
    view = public_dataset(record, context)
    binding = resolve_binding(record, ref, context, policy, current_file=candidate.file)
    return ContractExamples(context=context, storage=policy, registration=request, candidate=candidate,
        record=record, public_view=view, binding=binding, page={'items':[view]})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write',action='store_true',help='Write reproducible draft schema and synthetic examples in the repository.')
    args=parser.parse_args()
    bundle=example();value=bundle.model_dump(mode='json');schema=ContractExamples.model_json_schema()
    schema.update({'$schema':'https://json-schema.org/draft/2020-12/schema',
        '$id':'urn:dtest:dataset-registry:v1-draft:offline-bundle'})
    if args.write:
        ROOT.mkdir(parents=True,exist_ok=True);(ROOT/'examples').mkdir(exist_ok=True)
        (ROOT/'dataset-contract.schema.json').write_text(json.dumps(schema,ensure_ascii=False,indent=2)+'\n')
        (ROOT/'examples/project-reuse.json').write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    checked_schema=json.loads((ROOT/'dataset-contract.schema.json').read_text())
    checked_value=json.loads((ROOT/'examples/project-reuse.json').read_text())
    assert schema==checked_schema, 'Draft schema differs from contract models; export it explicitly'
    assert value==checked_value, 'Example differs; export it explicitly'
    ContractExamples.model_validate(checked_value)
    Draft202012Validator(checked_schema).validate(checked_value)
    print(json.dumps({'passed':True,'contract':'dataset-registry.v1-draft','schema_definitions':len(schema['$defs']),
        'actual_executor_calls':0,'actual_dataset_files_read':0,'runtime_integrated':False},ensure_ascii=False))


if __name__=='__main__':main()
