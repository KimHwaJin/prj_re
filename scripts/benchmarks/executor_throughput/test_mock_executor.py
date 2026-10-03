"""Contract checks: no execution, immutable outputs, version/idempotency and history."""
import asyncio, importlib.util, json, shutil, tempfile
from pathlib import Path
from uuid import uuid4
from types import SimpleNamespace
import httpx, pytest
from integrations.executor.observations import read_operation_observations
from service_contracts.events import ExecutorEvent

spec=importlib.util.spec_from_file_location('executor_fixture',Path(__file__).with_name('mock_executor.py'))
fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)

def body():
    return {'idempotency_key':str(uuid4()),'lifecycle':{'operation_mode':'MULTI','operation_wait_timeout_seconds':120},
        'trigger':{'type':'INTERACTIVE','actor':{'type':'AGENT','id':'test'}},'runtime':{'type':'JUPYTER','profile':'default'},
        'context':{k:'test' for k in ('user_id','task_id','project_id','session_id','workflow_id')},
        'operation':{'operation_timeout_seconds':120,'spec':{'schema_version':'1.0','steps':[{'sequence':0,
            'payload':{'type':'PYTHON_EXECUTE','source':{'type':'INLINE','content':
                "raise RuntimeError('Submitted code must never execute')\nprint({'step_id':'load','summary':{}})"}},
            'lineage':{'skill_name':'data_load','tool_name':'data_load','input_parameters':{}}}]}}}

class Broker:
    def __init__(self):self.events=[]
    async def xadd(self,stream,fields):self.events.append(ExecutorEvent.from_redis(fields));return '1-0'
    async def aclose(self):pass

@pytest.mark.asyncio
async def test_actual_contract_idempotency_versions_manifest_and_replay(monkeypatch):
    root=Path(tempfile.mkdtemp(prefix='executor-fixture-',dir='/private/tmp'));broker=Broker()
    monkeypatch.setattr(fixture.redis,'from_url',lambda *a,**k:broker)
    app=fixture.create_app({'result_root':str(root),'redis_url':'redis://test','stream':'test'})
    try:
        async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            await client.post('/_bench/reset');data=body()
            r=await client.post('/api/v1/executions',json=data);assert r.status_code==202
            receipt=r.json();eid=receipt['execution_id']
            assert (await client.post('/api/v1/executions',json=data)).json()==receipt
            changed={**data,'runtime':{'type':'JUPYTER','profile':'other'}}
            assert (await client.post('/api/v1/executions',json=changed)).status_code==409
            async with asyncio.timeout(3):
                while not any(e.event_type=='execution.operation_completed' for e in broker.events):await asyncio.sleep(.01)
            completion=broker.events[-1].model_dump(mode='json')
            step=receipt['operation']['steps'][0]
            expected=[{'sequence':0,'executor_step_id':step['step_id'],'plan_step_id':'load','tool_id':'data_load'}]
            observations=read_operation_observations(SimpleNamespace(executor_shared_result_root=root,agent_observation_max_chars=4000),completion,expected)
            assert observations[0]['summary']['label'].startswith('SYNTHETIC')
            assert len(list(root.rglob('source.py')))==1 # Duplicate submit did not write again.
            finalize={'idempotency_key':str(uuid4()),'actor':{'type':'AGENT','id':'test'},'expected_version':2}
            assert (await client.post(f'/api/v1/executions/{eid}/finalize',json=finalize)).status_code==409
            finalize['expected_version']=completion['payload']['continuation']['expected_version']
            finished=await client.post(f'/api/v1/executions/{eid}/finalize',json=finalize);assert finished.status_code==202
            assert (await client.post(f'/api/v1/executions/{eid}/finalize',json=finalize)).json()==finished.json()
            events=(await client.get(f'/api/v1/executions/{eid}/events')).json()['events']
            assert [e['event_sequence'] for e in events]==list(range(1,len(events)+1))
            assert events[-1]['event_type']=='execution.completed'
            replay=(await client.get(f'/api/v1/executions/{eid}/events',params={'after_sequence':2,'limit':2})).json()
            assert [e['event_sequence'] for e in replay['events']]==[3,4] and replay['has_more']
            stdout=next(root.rglob('stdout.txt'));stdout.write_bytes(b'corrupt output')
            with pytest.raises(ValueError,match='integrity mismatch'):
                read_operation_observations(SimpleNamespace(executor_shared_result_root=root,agent_observation_max_chars=4000),completion,expected)
    finally:shutil.rmtree(root)


def test_observation_step_is_static_unique_literal():
    with pytest.raises(ValueError):fixture.logical_step("print({'step_id':str(1),'summary':{}})")
    with pytest.raises(ValueError):fixture.logical_step("print({'step_id':'a','summary':{}},{'step_id':'b','summary':{}})")
