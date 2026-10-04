"""Owned loopback HTTP/PG/Executor verification with a REAL model.

Only the corporate employee verdict is a fixture. Model replies and analysis
Tool execution are never substituted. Raw replies go to a private 0600 file;
configuration/API keys are not included. Each scenario has a fresh session.
"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import socket
import tempfile
import time
from uuid import uuid4

import httpx
from dotenv import dotenv_values
import uvicorn

from cookie_auth import install_employee_fixture, sign_in, write_private_result
from serve_test_console import temporary_database, command, ConsoleServer
from verify_api_contract_flow import settings_for_test, migrate
from service_bootstrap import create_app
from service_settings import load_settings

# Explicit allowlist: never inherit existing external databases, streams or SSO.
MODEL_KEYS = ('MODEL_NAME', 'API_BASE_URL', 'MODEL_API_KEY', 'MODEL_ENABLE_THINKING',
              'MODEL_TIMEOUT_SECONDS', 'MODEL_MAX_RETRIES', 'MODEL_TEMPERATURE',
              'MODEL_STRUCTURED_OUTPUT_MODE')
REQUESTS = {
    'autofill': 'default-nce의 max_val 컬럼만 기초 통계 분석해줘. 등록된 툴을 사용해서 실행 계획을 제안해줘.',
    'missing': '기초 통계 분석 계획을 만들어줘. 아직 분석할 데이터는 선택하지 않았으니 임의로 고르지 말고, 내가 확인하거나 입력할 수 있게 해줘.',
    'edit': 'default-nce의 max_val과 min_val 두 컬럼에 대한 기초 통계 분석 계획을 제안해줘. 통계 결과를 Markdown 리포트로 설명해줘.',
    'decision': 'default-nce의 max_val 컬럼에 대한 기초 통계를 계산한 뒤, 실제 통계 결과를 보고 iqr와 zscore 중 이상치 탐지 방법을 결정해서 max_val만 확인해줘. 등록된 스킬과 툴을 사용하는 MULTI 계획으로 제안하고, 결과는 Markdown 리포트로 설명해줘. 아직 결과를 읽기 전이므로 탐지 방법은 미리 확정하지 마.',
}


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-env', required=True, type=Path)
    parser.add_argument('--executor-shared-root', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--port', type=int, default=18102)
    parser.add_argument('--db-port', type=int, default=53603)
    parser.add_argument('--scenario', choices=[*REQUESTS, 'all'], default='all')
    parser.add_argument('--planning-only', action='store_true', help='No plan approval/Executor submission')
    return parser.parse_args()


def observe(report, active):
    # Observe the public middleware/HTTP entry points without fabricating output.
    from agent_service.middleware.prompt_json import PromptJsonMiddleware
    old_model = PromptJsonMiddleware.awrap_model_call
    async def model(self, request, handler):
        async def measured(actual):
            started = time.perf_counter()
            item = {'scenario': active['name'], 'schema': self.schema.__name__,
                    'validation_feedback': [m.content for m in actual.messages
                        if isinstance(m.content, str) and m.content.startswith('The previous response failed')]}
            report['model_calls'].append(item)
            try:
                response = await handler(actual)
                item['reply'] = response.result[-1].model_dump(mode='json')
                return response
            except Exception as exc:
                item['error_type'] = type(exc).__name__
                raise
            finally:
                item['seconds'] = round(time.perf_counter() - started, 3)
                print(json.dumps({'scenario': active['name'], 'phase': 'model_response', 'schema': self.schema.__name__, 'seconds': item['seconds'], 'correction': bool(item['validation_feedback'])}, ensure_ascii=False), flush=True)
        return await old_model(self, request, measured)
    PromptJsonMiddleware.awrap_model_call = model

    from integrations.executor.client import ExecutorClient
    old_http = ExecutorClient.request
    async def executor(self, method, url, payload=None):
        reply = await old_http(self, method, url, payload)
        if method == 'POST':
            from urllib.parse import urlsplit
            report['executor_calls'].append({'scenario': active['name'],
                'path': urlsplit(url).path, 'status': reply['status_code'], 'payload': deepcopy(payload)})
        return reply
    ExecutorClient.request = executor

    from agent_service.agents.analysis.planning.runtime import PlanningRuntime
    old_role = PlanningRuntime.execution_role
    async def role(self, name, state, payload):
        item = {'scenario': active['name'], 'role': name,
                'completed_steps': list(state.get('completed_steps', [])), 'input': deepcopy(payload)}
        report['execution_roles'].append(item)
        reply = await old_role(self, name, state, payload)
        item['reply'] = reply.model_dump(mode='json')
        return reply
    PlanningRuntime.execution_role = role


async def verify(args, config, namespace, report):
    settings = load_settings(config=config, environ={})
    app = create_app(settings)
    install_employee_fixture(app, namespace)
    server = ConsoleServer(uvicorn.Config(app, host='127.0.0.1', port=args.port,
                                          log_level='warning', access_log=False))
    active = {'name': 'setup'}
    observe(report, active)
    task = asyncio.create_task(server.serve())
    def save():
        write_private_result(args.output, report)
    def check(case, name, passed, **facts):
        case['checks'].append({'name': name, 'passed': bool(passed), **facts})
        print(json.dumps({'scenario': case['name'], 'check': name, 'passed': bool(passed), **facts}, ensure_ascii=False), flush=True)
        save()
    try:
        async with asyncio.timeout(30):
            while not server.started:
                if task.done():
                    await task
                await asyncio.sleep(.05)
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{args.port}', trust_env=False, timeout=240) as client:
            me, headers = await sign_in(client)
            async def api(method, path, body=None, expected=200):
                response = await client.request(method, '/api/v1'+path, json=body,
                    headers={**headers, **({'Idempotency-Key': str(uuid4())} if method == 'POST' else {})})
                if response.status_code != expected:
                    raise AssertionError((path, response.status_code, response.text[:2000]))
                return response.json() if response.content else None
            async def snapshot(sid):
                from api_service.services.agent_graph_service import runtime
                from agent_config import build_langgraph_thread_id
                async with runtime.open_graph() as graph:
                    return (await graph.aget_state({'configurable': {'thread_id': build_langgraph_thread_id(sid)}})).values
            names = list(REQUESTS) if args.scenario == 'all' else [args.scenario]
            for name in names:
                active['name'] = name
                case = {'name': name, 'request': REQUESTS[name], 'checks': []}
                report['scenarios'].append(case)
                sid = (await api('POST', '/projects/'+me['default_project_id']+'/sessions',
                    {'session_name': 'Real model '+name, 'settings': {'kernel_profile': 'default'}}, 201))['id']
                path = '/sessions/'+sid+'/runs'
                async def submit(body):
                    return await api('POST', path, body, 202)
                async def wait(rid, predicate=None, timeout=240):
                    async with asyncio.timeout(timeout):
                        while True:
                            value = await api('GET', path+'/'+rid)
                            if (predicate or (lambda r: r['status'] not in ('pending', 'running')))(value):
                                return value
                            await asyncio.sleep(.2)
                started = time.perf_counter()
                rid = (await submit({'input': {'content': [{'type': 'text', 'text': REQUESTS[name]}]}}))['run_id']
                case.update(session_id=sid, run_id=rid)
                print(json.dumps({'scenario': name, 'phase': 'planning'}, ensure_ascii=False), flush=True)
                try:
                    run = await wait(rid)
                    case.update(planning_seconds=round(time.perf_counter()-started, 3), initial_run=run)
                    state = await snapshot(sid)
                    check(case, 'no_execution_before_approval', not state.get('execution_id') and not state.get('executor_operation_number'))
                    if name == 'missing':
                        if run['status'] == 'success':
                            final = run.get('result', {}).get('final_response', {})
                            check(case, 'missing_dataset_not_guessed', final.get('status') == 'answer', response_kind='clarification')
                        elif run['status'] == 'waiting_input':
                            plan = run['interrupt'][0]['payload']['plans'][0]
                            data = [p for p in plan['inputs'] if p['kind'] == 'data_reference']
                            check(case, 'missing_dataset_not_guessed', bool(data) and all(not p['has_value'] and p['required'] for p in data), response_kind='empty_required_form')
                            action = {'action': 'approve_plan', 'plan_id': plan['plan_id'], 'plan_revision': plan['plan_revision']}
                            response = await client.post('/api/v1'+path, headers={**headers, 'Idempotency-Key': str(uuid4())},
                                json={'run_id': rid, 'resume_token': run['resume_token'], 'command': {'resume': action}})
                            after = await api('GET', path+'/'+rid)
                            check(case, 'incomplete_approval_rejected_unchanged', response.status_code == 422 and after['resume_token'] == run['resume_token'])
                        else:
                            check(case, 'missing_dataset_not_guessed', False, status=run['status'])
                        continue
                    check(case, 'valid_plan_returned', run['status'] == 'waiting_input', status=run['status'])
                    if run['status'] != 'waiting_input':
                        continue
                    plan = run['interrupt'][0]['payload']['plans'][0]
                    case['initial_plan'] = plan
                    data = [p for p in plan['inputs'] if p['kind'] == 'data_reference']
                    check(case, 'explicit_dataset_prefilled', bool(data) and all(p.get('value') == 'default-nce' for p in data))
                    stats = next((s for s in plan['steps'] if s['tool_id'] == 'compute_statistics'), None)
                    # Follow referenced Workflow input separately; form bindings remain read-only.
                    def resolved(step, parameter):
                        p = next((p for p in step['parameters'] if p['name'] == parameter), {})
                        return next((i.get('value') for i in plan['inputs'] if i['name'] == p.get('input_name')), None) if p.get('kind') == 'workflow_input' else p.get('value')
                    expected = ['max_val', 'min_val'] if name == 'edit' else ['max_val']
                    check(case, 'explicit_columns_prefilled', stats is not None and resolved(stats, 'columns') == expected, expected=expected, actual=resolved(stats, 'columns') if stats else None)
                    if args.planning_only:
                        continue
                    if not all(c['passed'] for c in case['checks']):
                        case['execution_not_attempted'] = 'Invalid/missing user-requested values are not silently repaired by this harness'
                        continue
                    if name == 'edit':
                        cols = next(p for p in stats['parameters'] if p['name'] == 'columns')
                        changes = {'input_values': {cols['input_name']: ['max_val']}} if cols['kind'] == 'workflow_input' else {
                            'step_changes': [{'step_id': stats['step_id'], 'parameter': 'columns', 'value': ['max_val']}]}
                        old = run['resume_token']
                        action = {'action': 'edit_plan', 'plan_id': plan['plan_id'], 'plan_revision': plan['plan_revision'], **changes}
                        await submit({'run_id': rid, 'resume_token': old, 'command': {'resume': action}})
                        run = await wait(rid, lambda r: r['status'] == 'waiting_input' and r['resume_token'] != old or r['status'] in ('success', 'error'))
                        plan = run['interrupt'][0]['payload']['plans'][0]
                        case['edited_plan'] = plan
                        stats = next(s for s in plan['steps'] if s['tool_id'] == 'compute_statistics')
                        check(case, 'human_columns_edit_preserved', resolved(stats, 'columns') == ['max_val'] and plan['plan_revision'] == case['initial_plan']['plan_revision']+1)
                    if name == 'decision':
                        outlier = next((s for s in plan['steps'] if s['tool_id'] == 'detect_outliers'), None)
                        deferred = next((p for p in (outlier or {}).get('parameters', []) if p['name'] == 'method'), {})
                        check(case, 'method_deferred_until_results', plan['execution']['mode'] == 'MULTI' and deferred.get('kind') == 'deferred' and not deferred.get('has_value'))
                        if not case['checks'][-1]['passed']:
                            continue
                    before = len(report['executor_calls'])
                    started = time.perf_counter()
                    action = {'action': 'approve_plan', 'plan_id': plan['plan_id'], 'plan_revision': plan['plan_revision']}
                    await submit({'run_id': rid, 'resume_token': run['resume_token'], 'command': {'resume': action}})
                    old_token = run['resume_token']
                    async with asyncio.timeout(240):
                        while True:
                            run = await wait(rid, lambda r: r['status'] in ('success', 'error', 'canceled', 'recovery_required', 'timeout') or
                                r['status'] == 'waiting_input' and r['resume_token'] != old_token)
                            if run['status'] != 'waiting_input':
                                break
                            interaction = run['interrupt'][0]
                            if interaction['kind'] != 'decision_review':
                                break
                            decisions = interaction['payload']['decisions']
                            if not all(d.get('has_value') for d in decisions):
                                break  # Never manufacture a result-dependent choice.
                            case.setdefault('human_decision_confirmations', []).append(deepcopy(interaction))
                            old_token = run['resume_token']
                            await submit({'run_id': rid, 'resume_token': old_token, 'command': {'resume': {
                                'action': 'approve_decisions', 'interaction_id': interaction['interaction_id'],
                                'revision': interaction['revision'], 'values': {d['decision_id']: d['value'] for d in decisions}}}})
                    case.update(execution_seconds=round(time.perf_counter()-started, 3), terminal_run=run)
                    check(case, 'actual_executor_success', run['status'] == 'success' and run.get('result', {}).get('final_response', {}).get('executor_status') == 'SUCCEEDED', status=run['status'])
                    state = await snapshot(sid)
                    case['execution_decisions'] = state.get('execution_decisions')
                    if run['status'] != 'success':
                        continue
                    final = run['result']['final_response']
                    case['final_response'] = final
                    if name in ('autofill', 'edit'):
                        observed = next(o for o in final['observations'] if o['step_id'] == stats['step_id'])
                        keys = list(observed['summary']['items']['statistics']['items'])
                        calls = report['executor_calls'][before:]
                        serialized = json.dumps(calls, ensure_ascii=False)
                        check(case, 'only_requested_column_computed', keys == ['max_val'], actual=keys)
                        check(case, 'columns_reached_actual_executor', "columns=['max_val']" in serialized)
                    if name == 'decision':
                        reviews = [r for r in report['execution_roles'] if r['scenario'] == name and r['role'] == 'review']
                        check(case, 'decision_reads_completed_evidence', bool(reviews) and all(set(c['evidence_steps']) <= set(r['completed_steps']) for r in reviews for c in r['reply']['choices']))
                        check(case, 'decision_resolved_after_execution', bool(state.get('execution_decisions')) and all(v in ('iqr', 'zscore') for v in state['execution_decisions'].values()))
                    if name == 'edit':
                        case['followups'] = []
                        for question in ('방금 결과의 의미와 한계를 비전문가에게 설명해줘. 새 분석이나 계산은 필요 없어.',
                            '방금 결과를 Markdown 리포트로 다시 작성해줘. 로드 과정 설명은 빼고 통계 해석과 한계를 부각해줘. 새 계산이나 파일 등록은 하지 마.'):
                            active['name'] = 'followup'
                            count = len(report['executor_calls']); started = time.perf_counter()
                            follow_id = (await submit({'input': {'content': [{'type': 'text', 'text': question}]}}))['run_id']
                            following = await wait(follow_id)
                            values = await snapshot(sid)
                            item = {'request': question, 'run': following, 'seconds': round(time.perf_counter()-started, 3)}
                            case['followups'].append(item)
                            result = following.get('result', {}).get('final_response', {})
                            check(case, 'followup_grounded_without_executor', following['status'] == 'success' and result.get('status') == 'answer' and '근거 Step:' in result.get('message', '') and len(report['executor_calls']) == count and not values.get('execution_id') and not values.get('executor_operation_number'))
                            if len(case['followups']) == 2:
                                import re
                                prose = result.get('message', '').split('\n\n## 확인된 출력값', 1)[0]
                                check(case, 'report_revision_contains_actual_markdown_body',
                                    len(prose) >= 200 and len(re.findall(r'^#{1,3}\s+\S', prose, re.MULTILINE)) >= 2,
                                    prose_chars=len(prose))
                        active['name'] = name
                except Exception as exc:
                    case['exception'] = {'type': type(exc).__name__, 'detail': str(exc)[:3000]}
                    check(case, 'scenario_completed', False, exception_type=type(exc).__name__)
                finally:
                    latest = await api('GET', path+'/'+rid)
                    if latest['status'] not in ('success', 'error', 'canceled', 'recovery_required', 'timeout'):
                        await api('POST', path+'/'+rid+'/cancel', {}, 202)
                    save()
            report['passed'] = all(c['passed'] for case in report['scenarios'] for c in case['checks'])
            report['elapsed_seconds'] = round(time.perf_counter()-report.pop('_started'), 3)
            save()
    finally:
        server.should_exit = True
        await task
        from redis.asyncio import Redis
        redis = Redis.from_url(config['REDIS_URL'])
        try:
            await redis.xgroup_destroy(settings.worker.executor_event_stream, settings.worker.event_group)
            keys = [key async for key in redis.scan_iter(match=namespace+':*')]
            if keys:
                await redis.delete(*keys)
        finally:
            await redis.aclose()


def main():
    args = arguments()
    namespace = 'real-model-parameters-'+uuid4().hex[:12]
    container = 'dtest-'+namespace
    report = {'namespace': namespace, 'passed': False, '_started': time.perf_counter(), 'scenarios': [],
              'model_calls': [], 'executor_calls': [], 'execution_roles': [],
              'boundaries': {'model': 'actual_openai_compatible', 'sso': 'employee_verdict_fixture',
                  'database': 'owned_temporary_postgresql', 'executor': 'actual_local', 'phoenix': 'disabled', 'browser': 'not_tested'}}
    original = socket.getaddrinfo
    socket.getaddrinfo = lambda host, *a, **kw: original({'model.frodo.com': '10.250.110.99'}.get(host, host), *a, **kw)
    with tempfile.TemporaryDirectory(prefix=namespace+'-') as workspace:
        try:
            values = temporary_database(args, container)
            values.update(EXECUTOR_BASE_URL='http://127.0.0.1:8000', REDIS_URL='redis://127.0.0.1:6379/0',
                EXECUTOR_SHARED_RESULT_ROOT=str(args.executor_shared_root.resolve()), WORKFLOW_STORAGE_ROOT=str(Path(workspace)/'workflows'),
                ANALYSIS_DATASETS={'default-nce': {'title': 'NCE local sample', 'scope': 'GLOBAL',
                    'runtime_path': '/workspace/pv/default_data/df_nce_long_format.parquet'}})
            config = settings_for_test(values, namespace, args.port)
            private = dotenv_values(args.model_env, interpolate=False)
            config.update({k: private[k] for k in MODEL_KEYS if private.get(k) is not None})
            config['MODEL_PROVIDER'] = 'openai_compatible'
            if config['API_BASE_URL'] == 'http://fixture.invalid/v1' or config['MODEL_NAME'] == 'contract-fixture':
                raise ValueError('Actual model settings required')
            report['model'] = config['MODEL_NAME']
            migrate(config)
            asyncio.run(verify(args, config, namespace, report))
        finally:
            import subprocess
            subprocess.run(['docker', 'stop', container], capture_output=True, text=True)
    print(json.dumps({'passed': report['passed'], 'output': str(args.output)}, ensure_ascii=False), flush=True)
    raise SystemExit(0 if report['passed'] else 1)


if __name__ == '__main__':
    main()
