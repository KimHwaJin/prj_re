"""Local integration harness; see docs/agentic-executor-runtime.md."""
import asyncio, json, socket, time, subprocess, os, sys
from pathlib import Path
from uuid import uuid4, UUID
import httpx, uvicorn, yaml
from service_settings import load_settings
from service_bootstrap import create_app
from api_service.core.database import get_session_factory
from api_service.services.user_service import UserService
from api_service.schemas.common.user_schema import UserCreate
from api_service.services.agent_graph_service import runtime

parser = __import__('argparse').ArgumentParser(description='Isolated local HTTP/Executor/Jupyter verification; no production DBs.')
parser.add_argument('--settings-file', required=True, type=Path, help='Private flat JSON central settings mapping. Include local DB URLs and, for real mode, model/Phoenix settings.')
parser.add_argument('--output', required=True, type=Path, help='Result JSON path; contains observations/report, never configuration secrets.')
parser.add_argument('--real', action='store_true', help='Use supplied real model settings instead of explicit mock.')
parser.add_argument('--port', type=int, default=18091)
parser.add_argument('--fixture-plan',action='store_true',help='Use an explicit registered quality-review initial plan; execution/review/report still follow the selected provider.')
parser.add_argument('--followup-checks',action='store_true',help='After completion, test real-model explanation and Markdown revision on the same session without resubmission. Requires --real.')
args = parser.parse_args()
assert not args.followup_checks or args.real, 'Follow-up semantic checks require the real model; mock output is not evidence of understanding'
root = Path(__file__).resolve().parents[2]
config = json.loads(args.settings_file.read_text())
real_mode = args.real
original = socket.getaddrinfo
aliases = {'model.frodo.com':'10.250.110.99','phoenix.frodo.com':'10.250.110.100'}
socket.getaddrinfo = lambda host,*a,**kw: original(aliases.get(host,host),*a,**kw)
namespace = 'agentic-exec-'+uuid4().hex[:10]
real = config
base = config.get('EXECUTOR_BASE_URL', 'http://127.0.0.1:8000')
from urllib.parse import urlparse
assert urlparse(base).hostname in {'localhost', '127.0.0.1'}, 'Only local Executor is permitted'
assert config.get('EXECUTOR_SHARED_RESULT_ROOT'), 'Set local Executor shared_dir mount'
from sqlalchemy.engine import make_url
crud = config.get('database_url', config.get('DATABASE_URL'))
checkpoint = config.get('checkpoint_db_uri', config.get('CHECKPOINT_DB_URI'))
assert make_url(crud).database=='agentic_runtime_test'
assert make_url(checkpoint).database=='agentic_checkpoint_test'
assert all(make_url(v).host in {'127.0.0.1','localhost'} for v in (crud,checkpoint))
config.update(MODEL_PROVIDER='openai_compatible' if real_mode else 'mock',
    PHOENIX_PROJECT_NAME=namespace, EXECUTOR_SUBMIT_ENABLED=True,
    EXECUTOR_BASE_URL=base, EXECUTOR_SOURCE_TYPE='INLINE', EXECUTOR_RUNTIME_PROFILE='default',
    EXECUTOR_OPERATION_TIMEOUT_SECONDS=120, EXECUTOR_OPERATION_WAIT_TIMEOUT_SECONDS=120,
    REDIS_URL='redis://127.0.0.1:6379/0', EW_NAMESPACE=namespace,
    EW_EXECUTOR_BASE_URL=base.rstrip('/')+'/api/v1', EW_HEALTH_PORT=0,
    EW_CONCURRENCY=2, EW_POOL_SIZE=4, EW_POLL_SECONDS=.1, EW_IDLE_POLL_SECONDS=.2,
    EVENT_WORKER_ENABLED=True, AGENT_WORKER_ENABLED=True, AGENT_WORKER_CONCURRENCY=2,
    AGENT_WORKER_POLL_INTERVAL_SECONDS=.1, TASK_RECONCILER_ENABLED=False,
    CHECKPOINT_SETUP_ON_START=True, MAX_PLAN_CANDIDATES=1)
assert 'default-nce' in config.get('ANALYSIS_DATASETS', {}), 'Declare trusted default-nce test dataset'
# Only isolated Agent databases are migrated; no Executor database DDL.
import tempfile
with tempfile.TemporaryDirectory(prefix='agentic-exec-migrations-') as directory:
    migration = Path(directory)/'config.yml'
    migration.write_text(yaml.safe_dump({'service':config})); migration.chmod(0o600)
    for ini in ('alembic.crud.ini','alembic.ini'):
        outcome = subprocess.run([sys.executable,'-m','alembic','-c',ini,'upgrade','head'],cwd=root,
            env={**os.environ,'SERVICE_CONFIG_FILE':str(migration),'PYTHONPATH':str(root/'src')},capture_output=True,text=True)
        if outcome.returncode: raise RuntimeError('Isolated migration failed: '+outcome.stderr[-2000:])
settings=load_settings(config=config,environ={})
app=create_app(settings)
summary={'real_llm':real_mode,'initial_plan_fixture':args.fixture_plan,'namespace':namespace,'port':args.port,'passed':False}
if args.fixture_plan:
    from agent_service.agents.analysis.planning.runtime import PlanningRuntime
    from agent_service.agents.analysis.agent_builders.conversation.agent import reply_schema
    old_respond=PlanningRuntime.respond
    async def fixed_initial_plan(self,state,context,datasets):
        if state['user_request'].startswith('default-nce 데이터의 품질, 기본 통계, 이상치를 분석하고'):
            document=json.loads((root/'src/agent_service/agents/analysis/planning/fixtures/quality-review.json').read_text())
            return reply_schema(self.catalog,1)(kind='plans',message='명시적인 등록 Tool 검증 계획입니다.',
                plans=[{'definition':document,'input_values':{'dataset':'default-nce'}}])
        return await old_respond(self,state,context,datasets)
    PlanningRuntime.respond=fixed_initial_plan
context_deliveries=[]
if args.followup_checks:
    from agent_service.middleware import SessionAnalysisMiddleware
    old_wrap=SessionAnalysisMiddleware.awrap_model_call
    async def observe_context(self,request,handler):
        async def measured(actual):
            import json
            for message in actual.messages:
                if message.id=='dtest-session-analysis-context':
                    data=json.loads(message.content)['analysis']
                    context_deliveries.append({'execution_id':data['execution_id'],'source_run_id':data['source_run_id'],
                        'step_ids':[o['step_id'] for o in data['observations']],
                        'serialized_chars':len(json.dumps(data,ensure_ascii=False,separators=(',',':'))),
                        'omitted_observations':data['omitted_observations']})
            return await handler(actual)
        return await old_wrap(self,request,measured)
    SessionAnalysisMiddleware.awrap_model_call=observe_context

async def main():
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=args.port,log_level='warning',access_log=False))
    server_task=asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(30):
            while not server.started:
                if server_task.done(): await server_task
                await asyncio.sleep(.05)
        await asyncio.sleep(.5)
        async with get_session_factory()() as db:
            await UserService.bootstrap_admin(db,UserCreate(user_id='admin',user_name='Admin',role='admin'))
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{args.port}',trust_env=False,timeout=240) as client:
            uid=namespace
            r=await client.post('/api/v1/users',headers={'X-User-Id':'admin'},json={'user_id':uid,'user_name':'Executor E2E','role':'user'})
            assert r.status_code==201,r.text
            user=r.json(); headers={'X-User-Id':uid}
            r=await client.post('/api/v1/projects/'+user['default_project_id']+'/sessions',headers=headers,json={'session_name':'Executor 연계 검증','settings':{'kernel_profile':'default'}})
            assert r.status_code==201,r.text
            sid=r.json()['id']; path='/api/v1/sessions/'+sid+'/runs'
            request='default-nce 데이터의 품질, 기본 통계, 이상치를 분석하고 Markdown 리포트를 작성해줘. 등록된 스킬/툴을 사용하고, 기초 통계 실행 결과를 보고 이상치 방법을 결정하는 MULTI 계획으로 제안해줘. 가능하면 data_load → profile_data → compute_statistics → detect_outliers 순서로 진행해줘.'
            started=time.perf_counter()
            r=await client.post(path,headers={**headers,'Idempotency-Key':str(uuid4())},json={'input':{'content':[{'type':'text','text':request}]}})
            assert r.status_code==202,r.text
            rid=r.json()['id']; summary.update(run_id=rid,session_id=sid)
            async def read():
                response=await client.get(path+'/'+rid,headers=headers)
                assert response.status_code==200,response.text
                return response.json()
            async def wait_until(predicate,timeout):
                async with asyncio.timeout(timeout):
                    while True:
                        run=await read()
                        if predicate(run):return run
                        await asyncio.sleep(.15)
            run=await wait_until(lambda r:r['status'] not in ('pending','running'),240)
            assert run['status']=='waiting_input',run
            summary['planning_seconds']=round(time.perf_counter()-started,3)
            plan=run['interrupt'][0]['payload']['plans'][0]
            summary['tools']=[s['tool_id'] for s in plan['steps']]
            print(json.dumps({'phase':'approval','tools':summary['tools']},ensure_ascii=False),flush=True)
            action={'action':'approve_plan','plan_id':plan['plan_id'],'plan_revision':plan['plan_revision']}
            approval={'run_id':rid,'resume_token':run['resume_token'],'command':{'resume':action}}
            key=str(uuid4());started=time.perf_counter()
            r=await client.post(path,headers={**headers,'Idempotency-Key':key},json=approval)
            assert r.status_code==202,r.text
            run=await wait_until(lambda r:r['status']=='waiting_executor' or r['status'] in ('success','error','waiting_input'),40)
            summary['approval_to_wait_seconds']=round(time.perf_counter()-started,3)
            if run['status']=='waiting_executor':
                assert run['resume_token'] is None,run
                r=await client.post(path,headers={**headers,'Idempotency-Key':str(uuid4())},json={'input':{'content':[{'type':'text','text':'another request'}]}})
                summary['same_session_submission_status']=r.status_code
                assert r.status_code==409,r.text
            status_history=[run['status']]
            async with asyncio.timeout(240):
                while run['status'] not in ('success','error','canceled'):
                    if run['status']=='waiting_input':
                        interaction=run['interrupt'][0]
                        assert interaction['kind']=='decision_review',run
                        values={d['decision_id']:d['value'] for d in interaction['payload']['decisions'] if d.get('has_value')}
                        assert len(values)==len(interaction['payload']['decisions']),run
                        r=await client.post(path,headers={**headers,'Idempotency-Key':str(uuid4())},json={'run_id':rid,'resume_token':run['resume_token'],
                            'command':{'resume':{'action':'approve_decisions','interaction_id':interaction['interaction_id'],'revision':interaction['revision'],'values':values}}})
                        assert r.status_code==202,r.text
                    await asyncio.sleep(.2);run=await read()
                    if run['status']!=status_history[-1]:status_history.append(run['status'])
            summary.update(execution_seconds=round(time.perf_counter()-started,3),status_history=status_history,final_status=run['status'])
            assert run['status']=='success',run
            final=run['result']['final_response']
            assert final['status']=='analysis_completed' and final['executor_status']=='SUCCEEDED',final
            summary.update(execution_id=final['execution_id'],observations=final['observations'],report=final['report'],skipped_steps=final['skipped_steps'])
            repeat=await client.post(path,headers={**headers,'Idempotency-Key':key},json=approval)
            assert repeat.status_code==202,repeat.text
            async with runtime.open_graph() as graph:
                from agent_config import build_langgraph_thread_id
                snapshot=await graph.aget_state({'configurable':{'thread_id':build_langgraph_thread_id(sid)}})
                summary.update(operation_count=snapshot.values.get('executor_operation_number'),terminal_event_seen=snapshot.values.get('terminal_event_seen'))
            stream=await client.get(path+'/'+rid+'/stream',headers=headers)
            summary['sse_types']=sorted(set(line[7:] for line in stream.text.splitlines() if line.startswith('event: ')))
            assert 'message.completed' in summary['sse_types'] and 'interaction.resolved' in summary['sse_types']
            assert 'def data_load' not in stream.text and 'code_sha256' not in stream.text
            if args.followup_checks:
                from agent_config import build_langgraph_thread_id
                async with runtime.open_graph() as graph:
                    saved=(await graph.aget_state({'configurable':{'thread_id':build_langgraph_thread_id(sid)}})).values['last_analysis_context']
                assert saved['payload']['execution_id']==final['execution_id']
                summary['completed_analysis_context']={'source_run_id':saved['payload']['source_run_id'],
                    'step_ids':[o['step_id'] for o in saved['payload']['observations']],
                    'serialized_chars':len(json.dumps(saved['payload'],ensure_ascii=False,separators=(',',':'))),
                    'omitted_observations':saved['payload']['omitted_observations'],
                    'report_truncated':saved['payload']['report']['truncated']}
                summary['followups']=[]
                questions=[
                    '방금 분석에서 나온 기초 통계와 이상치 판단을 실제 결과에 근거해서 비전문가에게 설명해줘. 확인하지 않은 원인은 단정하지 말고, 코드 실행이나 새 분석 계획은 필요 없어.',
                    '방금 결과를 비전문가용 Markdown 리포트로 다시 작성해줘. 데이터 로드 과정 설명은 빼고 기초 통계 해석과 한계를 부각해줘. 새 계산이나 코드 실행은 하지 말고, 파일이나 Artifact 등록을 했다고 말하지 마.'
                ]
                for question in questions:
                    clock=time.perf_counter();before=len(context_deliveries)
                    r=await client.post(path,headers={**headers,'Idempotency-Key':str(uuid4())},
                        json={'input':{'content':[{'type':'text','text':question}]}})
                    assert r.status_code==202,r.text
                    following_id=r.json()['id']
                    async with asyncio.timeout(240):
                        while True:
                            following=(await client.get(path+'/'+following_id,headers=headers)).json()
                            if following['status'] not in ('pending','running'):break
                            await asyncio.sleep(.2)
                    result=following.get('result') or {}
                    summary['followups'].append({'request':question,'status':following['status'],
                        'result':result.get('final_response'),'seconds':round(time.perf_counter()-clock,3)})
                    assert following['status']=='success' and result['final_response']['status']=='answer',following
                    async with runtime.open_graph() as graph:
                        current=(await graph.aget_state({'configurable':{'thread_id':build_langgraph_thread_id(sid)}})).values
                    assert current['execution_id'] is None and current['executor_operation_number']==0
                    assert current['last_analysis_context']['payload']['execution_id']==final['execution_id']
                    deliveries=context_deliveries[before:]
                    assert deliveries and all(d['execution_id']==final['execution_id'] and d['source_run_id']==rid for d in deliveries)
                    assert all(d['serialized_chars']<=settings.agent.agent_session_analysis_max_chars for d in deliveries)
                summary['followup_context_deliveries']=context_deliveries
                summary['followup_created_execution']=False
            r=await client.post(path,headers={**headers,'Idempotency-Key':str(uuid4())},json={'input':{'content':[{'type':'text','text':'안녕하세요'}]}})
            summary['same_session_after_completion_status']=r.status_code
            assert r.status_code==202,r.text
            if not real_mode:
                next_id=r.json()['id']
                async with asyncio.timeout(15):
                    while True:
                        following=(await client.get(path+'/'+next_id,headers=headers)).json()
                        if following['status'] not in ('pending','running'):break
                        await asyncio.sleep(.1)
                assert following['status'] in ('waiting_input','success'),following
                summary['next_invocation_status']=following['status']
            else:
                # Admission after completion is checked here. Execution of the next
                # invocation is covered with real DB and an explicit model double.
                await client.post(path+'/'+r.json()['id']+'/cancel',headers=headers,json={})
        if real_mode:
            import api_service.observability.phoenix as phoenix
            provider=phoenix._tracer_provider
            if provider:await asyncio.to_thread(provider.force_flush)
            async with httpx.AsyncClient(trust_env=False,timeout=15) as client:
                for _ in range(15):
                    response=await client.get('http://phoenix.frodo.com/v1/projects/'+namespace+'/traces',headers={'Authorization':'Bearer '+(real.get('PHOENIX_API_KEY') or '')},params={'include_spans':True,'limit':100})
                    traces=response.json().get('data',[]) if response.status_code==200 else []
                    if traces:break
                    await asyncio.sleep(1)
                summary['phoenix_trace_count']=len(traces)
                summary['phoenix_project']=namespace
                assert traces,'No ingested Phoenix traces'
        summary['passed']=True
    except BaseException as exc:
        summary.update(error_type=type(exc).__name__,error=str(exc)[:3500])
        raise
    finally:
        server.should_exit=True
        await server_task
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
asyncio.run(main())
