"""Isolated HTTP/SQL/checkpoint/Redis + actual Executor/Jupyter repair verification.

Uses test-only registered functions and a preset plan to inject repeatable code
errors. --real selects a real repair role, not real planning; all other modes
use an explicit deterministic repair-role double. Production assets are unchanged.
"""
from cookie_auth import configure_cookie_auth, install_employee_fixture, sign_in, write_private_result
import argparse
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from sqlalchemy.engine import make_url
import uvicorn
import yaml

from service_settings import load_settings
from service_bootstrap import create_app
from api_service.services.agent_graph_service import runtime as graph_runtime
from agent_config import build_langgraph_thread_id
from agent_service.agents.analysis.tests.test_agentic_repair import FixtureCatalog,document,correction,approve
from agent_service.agents.analysis.agent_builders.conversation.agent import reply_schema

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--settings-file',required=True,type=Path)
parser.add_argument('--output',required=True,type=Path)
parser.add_argument('--real',action='store_true',help='Real model for repair role only; planning is a fixed fault-injection fixture.')
parser.add_argument('--port',type=int,default=18092)
parser.add_argument('--scenarios',nargs='+',choices=['binding','source','replan','custom','reject','exhausted','disabled','single'])
args=parser.parse_args()
scenarios=args.scenarios or (['binding'] if args.real else ['binding','source','replan','custom','reject','exhausted','disabled','single'])
config=json.loads(args.settings_file.read_text())
base=config.get('EXECUTOR_BASE_URL','http://127.0.0.1:8000')
assert urlparse(base).hostname in {'127.0.0.1','localhost'}
for key,name in [('database_url','agentic_runtime_test'),('checkpoint_db_uri','agentic_checkpoint_test')]:
    value=config.get(key,config.get(key.upper()))
    assert make_url(value).database==name and make_url(value).host in {'127.0.0.1','localhost'}
assert config.get('EXECUTOR_SHARED_RESULT_ROOT')
namespace='agentic-repair-'+uuid4().hex[:10]
original_dns=socket.getaddrinfo
aliases={'model.frodo.com':'10.250.110.99','phoenix.frodo.com':'10.250.110.100'}
socket.getaddrinfo=lambda host,*a,**kw:original_dns(aliases.get(host,host),*a,**kw)
config.update(MODEL_PROVIDER='openai_compatible' if args.real else 'mock',PHOENIX_PROJECT_NAME=namespace,
    EXECUTOR_SUBMIT_ENABLED=True,EXECUTOR_BASE_URL=base,EXECUTOR_SOURCE_TYPE='INLINE',EXECUTOR_RUNTIME_PROFILE='default',
    EXECUTOR_OPERATION_TIMEOUT_SECONDS=120,EXECUTOR_OPERATION_WAIT_TIMEOUT_SECONDS=180,
    REDIS_URL='redis://127.0.0.1:6379/0',EW_NAMESPACE=namespace,EW_EXECUTOR_BASE_URL=base.rstrip('/')+'/api/v1',EW_HEALTH_PORT=0,
    EW_CONCURRENCY=2,EW_POOL_SIZE=4,EW_POLL_SECONDS=.1,EW_IDLE_POLL_SECONDS=.2,
    EVENT_WORKER_ENABLED=True,AGENT_WORKER_ENABLED=True,AGENT_WORKER_CONCURRENCY=2,
    AGENT_WORKER_POLL_INTERVAL_SECONDS=.1,TASK_RECONCILER_ENABLED=False,CHECKPOINT_SETUP_ON_START=True,
    AGENT_REPAIR_LEVEL=0,AGENT_REPAIR_LEVEL_LIMIT=4,AGENT_MAX_REPAIR_ATTEMPTS=2)
root=Path(__file__).resolve().parents[2]
with tempfile.TemporaryDirectory(prefix='agentic-repair-migrations-') as temp:
    path=Path(temp)/'config.yml';path.write_text(yaml.safe_dump({'service':config}));path.chmod(0o600)
    for ini in ('alembic.crud.ini','alembic.ini'):
        outcome=subprocess.run([sys.executable,'-m','alembic','-c',ini,'upgrade','head'],cwd=root,
            env={**os.environ,'SERVICE_CONFIG_FILE':str(path),'PYTHONPATH':str(root/'src')},capture_output=True,text=True)
        if outcome.returncode:raise RuntimeError('Isolated migration failed: '+outcome.stderr[-1500:])
configure_cookie_auth(config,namespace,args.port)
settings=load_settings(config=config,environ={})
original_inputs=graph_runtime._load_graph_inputs
levels={'binding':1,'source':2,'replan':3,'custom':4,'reject':3,'exhausted':1,'disabled':0,'single':0}

def load_inputs():
    runtime,agent,kind=original_inputs()
    runtime.catalog=FixtureCatalog()
    async def preset(state,*rest):
        scenario=state['user_request'];doc=document(levels[scenario],0 if scenario in {'disabled','single'} else 2)
        if args.real:
            doc['goal']='Transform the supplied values using divisor 2 and compute their sum. Zero in the initial plan is an intentional diagnostic wiring defect.'
        if scenario=='single':doc['execution']['mode']='SINGLE'
        return reply_schema(runtime.catalog,1)(kind='plans',message='진단용 실패 주입 계획입니다.',plans=[{'definition':doc}])
    runtime.respond=preset
    if not args.real:
        original_role=runtime.execution_role
        async def role(name,state,payload):
            if name=='repair':
                return correction(levels[state['user_request']],payload,value=-(state.get('repair_attempts',0)+1) if state['user_request']=='exhausted' else 2)
            return await original_role(name,state,payload)
        runtime.execution_role=role
    return runtime,agent,kind

graph_runtime._load_graph_inputs=load_inputs
app=create_app(settings)
install_employee_fixture(app,namespace)
summary={'corporate_sdk':'verified_employee_fixture','production_cookie_csrf':True,'login_redis':'actual_loopback','passed':False,'namespace':namespace,'real_repair_llm':args.real,'planning':'preset_fault_injection',
    'executor':'actual_local_compose','scenarios':[]}

async def main():
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=args.port,log_level='warning',access_log=False))
    task=asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(30):
            while not server.started:
                if task.done():await task
                await asyncio.sleep(.05)
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{args.port}',trust_env=False,timeout=240) as client:
            user,headers=await sign_in(client)
            for scenario in scenarios:
                record={'scenario':scenario,'passed':False};summary['scenarios'].append(record)
                r=await client.post('/api/v1/projects/'+user['default_project_id']+'/sessions',headers=headers,
                    json={'session_name':scenario,'settings':{'kernel_profile':'default'}})
                assert r.status_code==201,r.text
                sid=r.json()['id'];path='/api/v1/sessions/'+sid+'/runs'
                r=await client.post(path,headers={**headers,'Idempotency-Key':str(uuid4())},json={'input':{'content':[{'type':'text','text':scenario}]}})
                assert r.status_code==202,r.text
                rid=r.json()['run_id'];record.update(session_id=sid,run_id=rid)
                async def read():
                    response=await client.get(path+'/'+rid,headers=headers);assert response.status_code==200,response.text
                    return response.json()
                async def wait(predicate,seconds=240):
                    async with asyncio.timeout(seconds):
                        while True:
                            run=await read()
                            if predicate(run):return run
                            await asyncio.sleep(.15)
                run=await wait(lambda r:r['status'] not in ('pending','running'))
                assert run['status']=='waiting_input',run
                plan=run['interrupt'][0]['payload']['plans'][0]
                r=await client.post(path,headers={**headers,'Idempotency-Key':str(uuid4())},json={'run_id':rid,'resume_token':run['resume_token'],
                    'command':{'resume':{'action':'approve_plan','plan_id':plan['plan_id'],'plan_revision':plan['plan_revision']}}})
                assert r.status_code==202,r.text
                started=time.perf_counter();reviews=[]
                async with asyncio.timeout(240):
                    while True:
                        run=await read()
                        if run['status'] in {'success','error','canceled','timeout','recovery_required'}:break
                        if run['status']=='waiting_input':
                            form=run['interrupt'][0];assert form['kind']=='repair_review',run
                            reviews.append(deepcopy(form))
                            command=approve(form,escalation=True,reject=scenario=='reject')
                            response=await client.post(path,headers={**headers,'Idempotency-Key':str(uuid4())},
                                json={'run_id':rid,'resume_token':run['resume_token'],'command':command})
                            assert response.status_code==202,response.text
                        await asyncio.sleep(.2)
                assert run['status']!='recovery_required',run
                final=run['result']['final_response'];record.update(final=final,public_status=run['status'],
                    seconds=round(time.perf_counter()-started,3),repair_review_count=len(reviews))
                succeeded=scenario in {'binding','source','replan','custom'}
                assert (final['status']=='analysis_completed')==succeeded,final
                async with graph_runtime.open_graph() as graph:
                    snapshot=await graph.aget_state({'configurable':{'thread_id':build_langgraph_thread_id(sid)}})
                state=snapshot.values;record.update(operation_count=state['executor_operation_number'],
                    terminal_event_seen=state['terminal_event_seen'],completed_steps=state['completed_steps'])
                assert state['terminal_event_seen'] and not snapshot.next
                assert sum(o['step_id']=='load' for o in state['observations'])==1
                if succeeded:
                    assert final['repair']['attempts']>=1
                    assert state['completed_steps']==['load','transform','finish']
                    assert final['observations'][-1]['summary']['items']['sum']==(12 if scenario=='source' else 6)
                elif scenario=='exhausted':assert final['repair']['attempts']==2 and final['repair']['stop_reason']=='attempts_exhausted'
                elif scenario=='reject':assert final['repair']['attempts']==0 and final['repair']['stop_reason']=='user_rejected'
                else:assert final['repair']['attempts']==0
                response=await client.get(path+'/'+rid+'/stream',headers=headers)
                assert 'def repair_transform' not in response.text and 'code_sha256' not in response.text
                record['sse_types']=sorted(set(line[7:] for line in response.text.splitlines() if line.startswith('event: ')))
                record['passed']=True
                print(json.dumps({'scenario':scenario,'passed':True,'seconds':record['seconds'],'operations':record['operation_count'],
                    'repair_attempts':final['repair']['attempts'],'executor_status':final['executor_status']},ensure_ascii=False),flush=True)
        summary['passed']=True
    except BaseException as exc:
        summary.update(error_type=type(exc).__name__,error=str(exc)[:3500]);raise
    finally:
        server.should_exit=True;await task
        graph_runtime._load_graph_inputs=original_inputs
        args.output.parent.mkdir(parents=True,exist_ok=True)
        write_private_result(args.output,summary)
asyncio.run(main())
