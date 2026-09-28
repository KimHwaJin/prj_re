"""Shared synchronous HTTP scenario for the bounded runner and Locust."""
import random
import time
from datetime import datetime, timezone
from uuid import UUID, uuid4

STAGES = [
    ('data_selection', None),
    ('analysis_context', 'mock'),
    ('workflow_candidate_selection', {'objective': 'EDA service load test'}),
    ('workflow_approval', {'candidate_number': 1}),
]


def prepare_user(request):
    user = request('POST', '/api/v1/users', json={'user_name': 'load-' + uuid4().hex})
    headers = {'Authorization': 'Bearer ' + user['user_id']}
    projects = request('GET', '/api/v1/projects', headers=headers)['items']
    return headers, next(p['id'] for p in projects if p['is_default'])


def execute(request, headers, project_id, *, record, timeout=120, poll_seconds=0.25, sleep=time.sleep, submit=False, observe_run=None):
    """Each stage waits for the completed Run and validates its actual interrupt."""
    begin = time.perf_counter()
    session = request('POST', f'/api/v1/projects/{project_id}/sessions', headers=headers,
                      json={'session_name': 'LLM mock load scenario'})['id']
    previous = None
    runs = []
    execution_id = None
    stages = STAGES + ([('executor_submitted', {'approved': True})] if submit else [])
    for expected, command in stages:
        payload = ({'input': {'messages': [{'role': 'user', 'content': '불량 예측을 위한 서비스 부하테스트'}]}}
                   if command is None else {'command': command, 'metadata': {'resume_run_id': previous}})
        started = time.perf_counter()
        submitted_at = datetime.now(timezone.utc).isoformat()
        run = request('POST', f'/api/v1/sessions/{session}/runs',
                      headers={**headers, 'Idempotency-Key': str(uuid4())}, json=payload)
        run_id = run['id']
        path = f'/api/v1/sessions/{session}/runs/{run_id}'
        deadline = time.monotonic() + timeout
        observed_at = datetime.now(timezone.utc).isoformat()
        polls = 0
        try:
            while run['status'] in ('pending', 'running'):
                if time.monotonic() >= deadline:
                    raise TimeoutError(f'{session}/{run_id}: {expected} timed out')
                sleep(poll_seconds)
                polls += 1
                run = request('GET', path, headers=headers)
                observed_at = datetime.now(timezone.utc).isoformat()
        finally:
            observation = {k: run.get(k) for k in ('id', 'task_id', 'attempt_count', 'created_at', 'started_at', 'updated_at', 'status')}
            observation.update(session_id=session, stage=expected, submitted_at=submitted_at,
                               observed_at=observed_at, poll_count=polls, poll_seconds=poll_seconds,
                               terminal=run['status'] not in ('pending', 'running'),
                               client_elapsed_ms=(time.perf_counter() - started) * 1000)
            if observe_run is not None:
                observe_run(observation)
        if run['status'] != 'interrupted':
            raise RuntimeError(f'{session}/{run_id}: {run["status"]}: {run.get("failure")}')
        actions = [a.get('name') for i in run.get('interrupt') or [] for a in i.get('action_requests', [])]
        if expected == 'executor_submitted':
            interrupts = run.get('interrupt') or []
            if len(interrupts) != 1 or interrupts[0].get('kind') != 'EXECUTOR_EVENT':
                raise RuntimeError(f'{session}/{run_id}: Executor submission not confirmed: {interrupts}')
            execution_id = str(UUID(interrupts[0].get('execution_id', '')))
        elif actions != [expected]:
            raise RuntimeError(f'{session}/{run_id}: expected {expected}, received {actions}')
        record('RUN/' + expected, (time.perf_counter() - started) * 1000)
        runs.append(observation)
        previous = run_id
    elapsed = (time.perf_counter() - begin) * 1000
    record('SCENARIO/' + ('executor_submit' if submit else 'approval_wait'), elapsed)
    return {'session_id':session,'elapsed_ms':elapsed,'runs':runs,'execution_id':execution_id}


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
