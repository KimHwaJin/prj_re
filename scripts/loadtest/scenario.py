"""Shared synchronous HTTP scenario for the bounded runner and Locust."""
import os
import random
import time
from datetime import datetime, timezone
from uuid import UUID, uuid4



def prepare_user(request):
    admin = os.environ.get('DTEST_LOADTEST_ADMIN_USER_ID')
    if not admin:
        raise RuntimeError('Set DTEST_LOADTEST_ADMIN_USER_ID to an existing test administrator.')
    public_id = 'load-' + uuid4().hex
    user = request('POST', '/api/v1/users', headers={'X-User-Id': admin},
                   json={'user_id': public_id, 'user_name': public_id, 'role': 'user'})
    return {'X-User-Id': user['user_id']}, user['default_project_id']


def execute(request, headers, project_id, *, record, timeout=120, poll_seconds=0.25, sleep=time.sleep, submit=False, observe_run=None):
    """Measure request → plan review. Executor submission returns in the next stage."""
    if submit:
        raise RuntimeError('Executor submission is not connected to the new planning runtime yet.')
    begin = time.perf_counter()
    session = request('POST', f'/api/v1/projects/{project_id}/sessions', headers=headers,
                      json={'session_name': 'Planning load scenario'})['id']
    run = request('POST', f'/api/v1/sessions/{session}/runs',
        headers={**headers, 'Idempotency-Key': str(uuid4())},
        json={'input': {'content': [{'type': 'text', 'text': '등록된 데이터의 품질과 이상치를 분석할 실행 계획을 제안해줘'}]}})
    submitted_at = datetime.now(timezone.utc).isoformat()
    deadline = time.monotonic() + timeout
    polls = 0
    while run['status'] in ('pending', 'running'):
        if time.monotonic() >= deadline:
            raise TimeoutError(f'{session}/{run["run_id"]}: planning timed out')
        sleep(poll_seconds)
        polls += 1
        run = request('GET', f'/api/v1/sessions/{session}/runs/{run["run_id"]}', headers=headers)
    if run['status'] != 'waiting_input' or len(run.get('interrupt') or []) != 1 or run['interrupt'][0].get('kind') != 'plan_review':
        raise RuntimeError(f'{session}/{run["run_id"]}: expected plan_review, received {run["status"]}')
    elapsed = (time.perf_counter() - begin) * 1000
    # Preserve the historical report record key; HTTP Run resources use run_id.
    observation = {'id': run['run_id'], **{k: run.get(k) for k in ('task_id', 'attempt_count', 'created_at', 'started_at', 'updated_at', 'status')}}
    observation.update(session_id=session, stage='plan_review', submitted_at=submitted_at,
                       observed_at=datetime.now(timezone.utc).isoformat(), poll_count=polls,
                       poll_seconds=poll_seconds, terminal=True, client_elapsed_ms=elapsed)
    if observe_run:
        observe_run(observation)
    record('RUN/plan_review', elapsed)
    record('SCENARIO/approval_wait', elapsed)
    return {'session_id': session, 'elapsed_ms': elapsed, 'runs': [observation], 'execution_id': None}


def create_random_resource(request, headers, projects, *, record, rng=random):
    """Equal probability of project creation or session creation in an owned project."""
    start = time.perf_counter()
    if rng.random() < 0.5:
        resource = request('POST', '/api/v1/projects', headers=headers,
                           json={'project_name': 'load-' + uuid4().hex})
        projects.append(resource['id'])
        # Bound client memory on long soaks; all created resources remain in the DB.
        if len(projects) > 1000:
            del projects[0]
        kind = 'project'
    else:
        project = rng.choice(projects)
        resource = request('POST', f'/api/v1/projects/{project}/sessions', headers=headers,
                           json={'session_name': 'load-' + uuid4().hex})
        kind = 'session'
    elapsed = (time.perf_counter() - start) * 1000
    record('SCENARIO/create_' + kind, elapsed)
    return {'resource_type': kind, 'resource_id': resource['id'], 'elapsed_ms': elapsed}
