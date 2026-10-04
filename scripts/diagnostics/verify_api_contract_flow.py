"""Loopback API contract verification: real DB/Worker/SSO cookie/Executor, fixed model transport.

The private settings file selects dedicated agentic_runtime_test and
agentic_checkpoint_test databases. Corporate employee verification is a fixture.
No production factory, registered Tool, Executor code or existing service is changed.
"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from sqlalchemy.engine import make_url
import uvicorn
import yaml

from cookie_auth import configure_cookie_auth, install_employee_fixture, sign_in, write_private_result
from service_settings import load_settings
from service_bootstrap import create_app

ROOT = Path(__file__).resolve().parents[2]
# Reuse the existing transport fixture; real create_agent and middleware still run.
sys.path.insert(0, str(ROOT / 'scripts/benchmarks/worker_e2e'))
from model_fixture import install_model_fixture


def settings_for_test(values, namespace, port):
    config = deepcopy(values)
    for key, database in (('DATABASE_URL', 'agentic_runtime_test'),
                          ('CHECKPOINT_DB_URI', 'agentic_checkpoint_test')):
        value = config.get(key, config.get(key.lower()))
        url = make_url(value)
        if url.host not in {'127.0.0.1', 'localhost'} or url.database != database:
            raise ValueError('Dedicated loopback test database required: ' + key)
    if urlsplit(config.get('EXECUTOR_BASE_URL', '')).hostname not in {'127.0.0.1', 'localhost'}:
        raise ValueError('Only local Executor is permitted')
    if urlsplit(config.get('REDIS_URL', '')).hostname not in {'127.0.0.1', 'localhost'}:
        raise ValueError('Only local Redis is permitted')
    # One canonical endpoint and one application/event command database.
    config.pop('EW_EXECUTOR_BASE_URL', None)
    config.pop('EW_REDIS_URL', None)
    config.pop('MODEL_CATALOG', None)
    config.pop('DEFAULT_MODEL', None)
    config['EW_DATABASE_URL'] = str(make_url(config.get('DATABASE_URL', config.get('database_url')))
                                  .set(drivername='postgresql').render_as_string(hide_password=False))
    config.update(MODEL_PROVIDER='openai_compatible', MODEL_NAME='contract-fixture',
        MODEL_API_KEY='not-a-secret', API_BASE_URL='http://fixture.invalid/v1',
        MODEL_STRUCTURED_OUTPUT_MODE='prompt_json', PHOENIX_ENDPOINT='',
        EXECUTOR_SUBMIT_ENABLED=True, EXECUTOR_SOURCE_TYPE='INLINE', EXECUTOR_RUNTIME_PROFILE='default',
        EXECUTOR_RUNTIME_PROFILES=['default'], EXECUTOR_OPERATION_TIMEOUT_SECONDS=120,
        EXECUTOR_OPERATION_WAIT_TIMEOUT_SECONDS=120, EW_NAMESPACE=namespace,
        EW_EVENT_GROUP_NAME=namespace+':ingress', EW_HEALTH_PORT=0, EW_INGRESS_CONCURRENCY=2,
        EW_POOL_SIZE=4, EW_POLL_SECONDS=.1, EW_IDLE_POLL_SECONDS=.2,
        EVENT_WORKER_ENABLED=True, AGENT_WORKER_ENABLED=True, AGENT_WORKER_CONCURRENCY=2,
        AGENT_WORKER_POLL_INTERVAL_SECONDS=.1, TASK_RECONCILER_ENABLED=False,
        CHECKPOINT_SETUP_ON_START=True, WORKFLOW_PERSISTENCE_ENABLED=False,
        WORKFLOW_RECOMMENDATION_ENABLED=False, MAX_PLAN_CANDIDATES=1,
        AGENT_PROJECT_MEMORY_MODE='manual', DEMO_ARTIFACTS_ENABLED=False,
        SHUTDOWN_DRAIN_SECONDS=0)
    configure_cookie_auth(config, namespace, port)
    return config


def migrate(config):
    with tempfile.TemporaryDirectory(prefix='api-flow-migration-') as directory:
        path = Path(directory) / 'config.yml'
        path.write_text(yaml.safe_dump({'service': config})); path.chmod(0o600)
        for ini in ('alembic.crud.ini', 'alembic.ini'):
            result = subprocess.run([sys.executable, '-m', 'alembic', '-c', ini, 'upgrade', 'head'],
                cwd=ROOT, env={'PATH': os.environ.get('PATH',''), 'SERVICE_CONFIG_FILE': str(path), 'PYTHONPATH': str(ROOT/'src')},
                capture_output=True, text=True)
            if result.returncode:
                raise RuntimeError('Dedicated migration failed: ' + result.stderr[-2000:])


def sse_events(text):
    return [json.loads(line[6:]) for line in text.splitlines()
            if line.startswith('data: ') and line[6:].strip()]


async def verify(args):
    namespace = 'api-contract-flow-' + uuid4().hex[:12]
    report = {'namespace': namespace, 'passed': False, 'checks': [], 'findings': [],
              'boundaries': {'corporate_sdk': 'verified_employee_fixture',
                'model': 'fixed_transport_real_agents_and_middleware', 'executor': 'actual_local',
                'api_database_and_worker': 'actual_dedicated_postgresql', 'login_and_streams': 'actual_local_redis',
                'artifact_registration': 'deferred_not_tested', 'frontend': 'HTTP_client_no_browser'}}
    def check(name, **facts):
        report['checks'].append({'name': name, 'passed': True, **facts})
        print(json.dumps({'check': name, 'passed': True}, ensure_ascii=False), flush=True)
    config = settings_for_test(json.loads(args.settings_file.read_text()), namespace, args.port)
    migrate(config)
    settings = load_settings(config=config, environ={})
    metrics = {'models': []}
    install_model_fixture({'model_delay_ms': 300}, metrics, lambda: True)
    # Observe actual HTTP calls. This wrapper does not fabricate Executor outcomes.
    from integrations.executor.client import ExecutorClient
    request = ExecutorClient.request
    calls = []
    async def observe(self, method, url, payload=None):
        result = await request(self, method, url, payload)
        if method == 'POST':
            calls.append({'path': urlsplit(url).path, 'status': result['status_code']})
        return result
    ExecutorClient.request = observe
    app = create_app(settings)
    install_employee_fixture(app, namespace)
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=args.port,
                                          log_level='warning', access_log=False))
    server_task = asyncio.create_task(server.serve())
    started = time.perf_counter()
    try:
        async with asyncio.timeout(30):
            while not server.started:
                if server_task.done(): await server_task
                await asyncio.sleep(.05)
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{args.port}', trust_env=False, timeout=180) as client:
            async def api(method, path, expected=200, *, body=None, headers=None):
                response = await client.request(method, '/api/v1'+path, json=body, headers=headers)
                assert response.status_code == expected, (path, response.status_code, response.text[:2000])
                return response.json() if response.content else None
            assert (await client.get('/api/v1/users/me')).status_code == 401
            me, headers = await sign_in(client)
            project = me['default_project_id']
            assert me['role'] == 'user'
            listing = await api('GET', '/projects')
            default = next(p for p in listing['items'] if p['id'] == project)
            assert set(default) == {'id', 'name', 'is_default', 'created_at', 'updated_at'}
            assert default['is_default']
            detail = await api('GET', '/projects/'+project)
            assert detail['system_prompt'] == '' and detail['prompt_version'] == 1
            await api('GET', '/users', 403)
            await api('POST', '/projects', 403, body={'project_name': 'No CSRF'})
            check('login_default_project_summary_detail_csrf_and_role')
            private = await api('POST', '/projects', 201, body={'project_name': namespace}, headers=headers)
            await api('PATCH', '/projects/'+private['id'], body={'system_prompt': '시험에서는 실제 실행 근거만 설명한다.'}, headers=headers)
            async def session(name):
                item = await api('POST', '/projects/'+private['id']+'/sessions', 201,
                    body={'session_name': name, 'settings': {'kernel_profile': 'default'}}, headers=headers)
                assert item['settings']['kernel_profile'] == 'default'
                assert item['availability']['allowed_actions'] == ['send_message']
                return item['id']
            sid, other = await session('analysis'), await session('other')
            rows = await api('GET', '/projects/'+private['id']+'/sessions')
            assert {sid, other} <= {r['id'] for r in rows['items']}
            check('project_create_prompt_and_session_list', session_count=2)
            async def availability(session_id, action):
                async with asyncio.timeout(30):
                    while True:
                        item = await api('GET', '/sessions/'+session_id)
                        if action in item['availability']['allowed_actions']:
                            if action=='send_message': assert item['active_run'] is None
                            return item
                        await asyncio.sleep(.05)
            async def submit(session_id, body, key=None, expected=202):
                return await api('POST', '/sessions/'+session_id+'/runs', expected,
                    body=body, headers={**headers, 'Idempotency-Key': key or str(uuid4())})
            async def text(session_id, message):
                await availability(session_id, 'send_message')
                return await submit(session_id, {'input': {'content': [{'type': 'text', 'text': message}]}})
            async def wait(session_id, rid, predicate):
                async with asyncio.timeout(180):
                    while True:
                        run = await api('GET', '/sessions/'+session_id+'/runs/'+rid)
                        if predicate(run): return run
                        if run['status'] in {'error', 'canceled'}:
                            raise AssertionError(('Unexpected terminal status', run))
                        await asyncio.sleep(.05)
            async def done(session_id, rid):
                item = await wait(session_id, rid, lambda r: r['status'] == 'success')
                await availability(session_id, 'send_message')
                return item
            answer = await text(sid, '[answer] 분석하지 말고 사용 안내만 알려줘')
            answer = await done(sid, answer['run_id'])
            assert answer['result']['final_response']['status'] == 'answer' and not calls
            check('plain_answer_without_hitl_or_executor', run_id=answer['run_id'])
            initial = await text(sid, 'default-nce 품질과 통계를 확인하고 보고서를 작성해줘')
            rid = initial['run_id']; report.update(session_id=sid, run_id=rid)
            path = '/sessions/'+sid+'/runs/'+rid
            # Connect while running, consume through the first HITL, then deliberately disconnect.
            live = []
            async with client.stream('GET', '/api/v1'+path+'/stream') as response:
                assert response.status_code == 200
                async with asyncio.timeout(30):
                    async for line in response.aiter_lines():
                        if not line.startswith('data: '): continue
                        event = json.loads(line[6:]); live.append(event)
                        if event.get('type') == 'interaction.opened': break
            assert live and any(e.get('type') == 'interaction.opened' for e in live)
            run = await wait(sid, rid, lambda r: r['status'] == 'waiting_input')
            stage = await availability(sid, 'respond_to_interaction')
            assert stage['active_run']['run_id'] == rid
            assert stage['availability']['allowed_actions'] == ['respond_to_interaction']
            await submit(sid, {'input': {'content': [{'type':'text','text':'새 요청'}]}}, expected=409)
            check('live_sse_hitl_disconnect_and_new_input_lock', events=len(live))
            old = deepcopy(run); plan = run['interrupt'][0]['payload']['plans'][0]
            readonly = {'action':'edit_plan','plan_id':plan['plan_id'],'plan_revision':plan['plan_revision'],
                'step_changes':[{'step_id':'load','parameter':'parquet_path','value':'/workspace/pv/private.parquet'}]}
            rejected = await submit(sid, {'run_id':rid,'resume_token':run['resume_token'],
                'command':{'resume':readonly}}, expected=422)
            unchanged = await api('GET', path)
            assert unchanged['resume_token']==run['resume_token'] and unchanged['interrupt']==run['interrupt']
            check('runtime_path_edit_rejected_without_mutation')
            columns = next(p for step in plan['steps'] if step['step_id']=='statistics'
                           for p in step['parameters'] if p['name']=='columns')
            assert columns['editable'] and columns['has_value'] and columns['value'] is None
            assert columns['origin']=='tool_default'
            old_token = run['resume_token']
            await submit(sid, {'run_id':rid,'resume_token':old_token,'command':{'resume':{
                'action':'edit_plan','plan_id':plan['plan_id'],'plan_revision':plan['plan_revision'],
                'step_changes':[{'step_id':'statistics','parameter':'columns','value':[args.statistics_column]}]}}})
            run = await wait(sid,rid,lambda r:r['status']=='waiting_input' and r['resume_token']!=old_token)
            plan = run['interrupt'][0]['payload']['plans'][0]
            columns = next(p for step in plan['steps'] if step['step_id']=='statistics'
                           for p in step['parameters'] if p['name']=='columns')
            assert columns['value']==[args.statistics_column] and columns['origin']=='user'
            check('registered_optional_parameter_visible_and_editable')
            editable = {'action':'edit_plan','plan_id':plan['plan_id'],'plan_revision':plan['plan_revision'],
                'step_changes':[{'step_id':'outliers','parameter':'method','value':'zscore'}]}
            await submit(sid, {'run_id':rid,'resume_token':run['resume_token'],'command':{'resume':editable}})
            run = await wait(sid,rid,lambda r:r['status']=='waiting_input' and r['resume_token']!=old['resume_token'])
            await availability(sid,'respond_to_interaction')
            plan = run['interrupt'][0]['payload']['plans'][0]
            method = next(p for s in plan['steps'] if s['step_id']=='outliers' for p in s['parameters'] if p['name']=='method')
            assert method['value']=='zscore' and method['editable']
            parameter_version=plan['plan_revision']
            before_exclusion=deepcopy(run)
            edit = {'action': 'edit_plan', 'plan_id': plan['plan_id'], 'plan_revision': plan['plan_revision'],
                    'input_values': {'dataset': 'default-nce'}, 'excluded_step_ids': ['outliers'],
                    'step_changes': []}
            editing = await submit(sid, {'run_id':rid, 'resume_token':run['resume_token'], 'command':{'resume':edit}})
            assert editing['run_id'] == rid
            run = await wait(sid, rid, lambda r:r['status']=='waiting_input' and r['resume_token']!=before_exclusion['resume_token'])
            await availability(sid, 'respond_to_interaction')
            plan = run['interrupt'][0]['payload']['plans'][0]
            assert plan['plan_revision'] == parameter_version + 1
            assert next(s for s in plan['steps'] if s['step_id']=='outliers')['status']=='excluded'
            check('plan_parameter_edit_tool_exclusion_and_stable_run_id', revision=plan['plan_revision'])
            saved = (run['run_id'], run['resume_token'], run['interrupt'])
            await api('POST', '/auth/logout', 204, headers=headers)
            await api('GET', path, 401)
            me, headers = await sign_in(client)
            reread = await api('GET', path)
            assert (reread['run_id'], reread['resume_token'], reread['interrupt']) == saved
            await submit(sid, {'run_id':rid,'resume_token':old['resume_token'], 'command':{'resume':edit}}, expected=409)
            check('login_renewal_preserves_hitl_and_rejects_stale_token')
            approval = {'run_id':rid,'resume_token':run['resume_token'], 'command':{'resume':{
                'action':'approve_plan','plan_id':plan['plan_id'],'plan_revision':plan['plan_revision']}}}
            key = str(uuid4())
            approved = await submit(sid, approval, key)
            assert approved['run_id'] == rid
            waiting = await wait(sid, rid, lambda r:r['status']=='waiting_executor')
            assert waiting['resume_token'] is None
            locked = await api('GET', '/sessions/'+sid)
            assert locked['availability']['status']=='busy' and not locked['availability']['allowed_actions']
            await submit(sid, {'input':{'content':[{'type':'text','text':'동일 세션 추가 요청'}]}}, expected=409)
            parallel = await text(other, '[answer] 별도 세션 사용 안내')
            parallel = await done(other, parallel['run_id'])
            assert parallel['result']['final_response']['status']=='answer'
            check('executor_wait_locks_same_session_but_other_session_runs')
            final = await done(sid, rid)
            result = final['result']['final_response']
            assert result['status']=='analysis_completed' and result['executor_status']=='SUCCEEDED'
            assert result['report']['status']=='ready' and result['report']['artifact_registration']=='deferred'
            # User exclusions are frozen before execution; skipped_steps only
            # describes runtime conditions/dependency skips of retained steps.
            report['findings'].append({'kind':'excluded_vs_skipped_steps',
                'description':'User exclusions live in the approved plan; final skipped_steps is not their combined list.',
                'user_excluded_step_ids':['outliers'],'runtime_skipped_step_ids':result['skipped_steps']})
            assert {o['step_id'] for o in result['observations']} == {'load','profile','statistics'}
            statistics = next(o for o in result['observations'] if o['step_id']=='statistics')
            assert list(statistics['summary']['items']['statistics']['items']) == [args.statistics_column]
            assert any(c['path'].endswith('/finalize') for c in calls)
            baseline = deepcopy(calls)
            repeated = await submit(sid, approval, key)
            assert repeated['run_id']==rid
            assert calls==baseline
            external = (await client.get(config['EXECUTOR_BASE_URL'].rstrip('/')+'/api/v1/executions/'+result['execution_id'])).json()
            assert external['state']['status']=='SUCCEEDED' and external['runtime']['session_id'] is None, external
            check('actual_executor_finalize_result_and_idempotent_approval', execution_id=result['execution_id'],
                  executed_steps=['load','profile','statistics'], report_chars=len(result['report']['content']),
                  artifact_registration='deferred')
            all_events = sse_events((await client.get('/api/v1'+path+'/stream')).text)
            seq = [e['sequence'] for e in all_events if 'sequence' in e]
            assert seq == sorted(set(seq))
            cursor = next(e['sequence'] for e in reversed(live) if 'sequence' in e)
            suffix = sse_events((await client.get('/api/v1'+path+'/stream', headers={'Last-Event-ID':str(cursor)})).text)
            assert [e['sequence'] for e in suffix if 'sequence' in e] == [n for n in seq if n>cursor]
            assert all(e['run_id']==rid for e in all_events if 'run_id' in e)
            serialized = json.dumps(all_events)
            assert 'def data_load' not in serialized and 'code_sha256' not in serialized
            check('sse_reconnect_exact_suffix_and_no_tool_code', total_events=len(seq), cursor=cursor)
            for label, question in [('explanation','[answer] 방금 실제 결과를 설명해줘'),
                                    ('report_revision','[answer] 방금 결과를 비전문가용 Markdown으로 다시 설명해줘')]:
                following = await text(sid, question)
                assert following['run_id'] != rid
                follow = await done(sid, following['run_id'])
                assert follow['result']['final_response']['status']=='answer'
                assert calls==baseline
                check(label+'_reuses_analysis_without_executor', new_run_id=following['run_id'])
            assert any(m['analysis_reference'] for m in metrics['models'] if m['role']=='answer')
            assert not any(c['path'].endswith('/artifacts') for c in calls)
            check('completed_session_accepts_input_and_report_file_registration_remains_deferred')
            again = await text(sid,'default-nce를 다시 분석하고 보고서를 작성해줘')
            next_run_id=again['run_id']
            next_waiting=await wait(sid,next_run_id,lambda r:r['status']=='waiting_input')
            await availability(sid,'respond_to_interaction')
            next_plan=next_waiting['interrupt'][0]['payload']['plans'][0]
            await submit(sid,{'run_id':next_run_id,'resume_token':next_waiting['resume_token'],'command':{'resume':{
                'action':'approve_plan','plan_id':next_plan['plan_id'],'plan_revision':next_plan['plan_revision'],
                'step_changes':[{'step_id':'outliers','parameter':'method','value':'zscore'}]}}})
            next_final=await done(sid,next_run_id)
            next_result=next_final['result']['final_response']
            assert next_result['executor_status']=='SUCCEEDED' and next_result['execution_id']!=result['execution_id']
            actual_outlier=next(o for o in next_result['observations'] if o['step_id']=='outliers')
            assert actual_outlier['status']=='SUCCEEDED' and 'zscore' in json.dumps(actual_outlier['summary'])
            check('new_analysis_creates_new_execution_and_edited_method_executes',
                execution_id=next_result['execution_id'], run_id=next_run_id,method='zscore')
            report.update(executor_posts=calls, model_roles=[m['role'] for m in metrics['models']],
                          final_status=final['status'], report_status=result['report']['status'])
        report['passed']=True
    except BaseException as exc:
        report.update(error_type=type(exc).__name__, error=str(exc)[:3500])
        raise
    finally:
        server.should_exit=True
        await server_task
        from agent_service.agents.analysis.planning import runtime
        for fixture_client in getattr(runtime, '_benchmark_fixture_clients', []):
            await fixture_client.aclose()
        ExecutorClient.request=request
        # Destroy only this diagnostic's group/login keys; never delete shared events.
        from redis.asyncio import Redis
        redis=Redis.from_url(config['REDIS_URL'])
        try:
            await redis.xgroup_destroy(settings.worker.executor_event_stream, settings.worker.event_group)
            keys=[key async for key in redis.scan_iter(match=namespace+':sso:*')]
            if keys: await redis.delete(*keys)
        finally: await redis.aclose()
        report['executor_posts']=calls
        report['elapsed_seconds']=round(time.perf_counter()-started,3)
        write_private_result(args.output, report)
        print(json.dumps({'passed':report['passed'],'checks':len(report['checks']),'output':str(args.output)}),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settings-file',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--port',type=int,default=18095)
    parser.add_argument('--statistics-column', default='max_val', help='A real column in the declared local NCE sample')
    asyncio.run(verify(parser.parse_args()))


if __name__=='__main__': main()
