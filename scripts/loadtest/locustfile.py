"""Scenario selection is available in the Locust start form (--scenario headlessly)."""
from pathlib import Path
import re
import os
import json
from datetime import datetime, timezone
import sys
import time
import gevent
from locust import HttpUser, between, constant, events, task
from locust.exception import StopUser

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scenario import create_random_resource, execute, prepare_user
from mixed_crud import MixedCRUD


@events.init_command_line_parser.add_listener
def options(parser):
    parser.add_argument('--poll-seconds', type=float, default=0.25, help='Run status polling interval in seconds')
    parser.add_argument('--scenario', choices=['crud', 'crud_mixed', 'submit', 'approval'], default='crud',
                        help='crud: create only; crud_mixed: create/read/update/delete 20/50/20/10; submit: approve and submit, then new session; approval: stop before approval')


class ServiceLoadUser(HttpUser):
    # Submission scenario immediately starts a new session after confirmed submission.
    wait_time = constant(0)

    def request_json(self, method, path, **kwargs):
        name = re.sub(r'[0-9a-f]{8}-[0-9a-f-]{27,}', ':id', path)
        response = self.client.request(method, path, name=name, timeout=30, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else None

    def on_start(self):
        self.scenario = self.environment.parsed_options.scenario
        self.poll_seconds = float(self.environment.parsed_options.poll_seconds)
        if self.poll_seconds <= 0:
            raise ValueError('poll-seconds must be positive')
        if self.scenario in ('crud', 'crud_mixed'):
            self.wait_time = lambda: between(0.5, 1.5)(self)
        try:
            self.headers, self.project = prepare_user(self.request_json)
            self.projects = [self.project]
            if self.scenario == 'crud_mixed':
                self.mixed = MixedCRUD(self.request_json, self.headers)
                self.mixed.seed()
        except Exception as exc:
            self.record('SETUP/user', 0, exc)
            raise StopUser()

    def record(self, name, milliseconds, exception=None):
        self.environment.events.request.fire(request_type='FLOW', name=name,
            response_time=milliseconds, response_length=0, exception=exception, context={})

    @task
    def scenario_iteration(self):
        start = time.perf_counter()
        metric = {'crud': 'crud', 'crud_mixed': 'crud_mixed', 'submit': 'executor_submit', 'approval': 'approval_wait'}[self.scenario]
        try:
            if self.scenario == 'crud_mixed':
                self.mixed.step(record=self.record)
            elif self.scenario == 'crud':
                create_random_resource(self.request_json, self.headers, self.projects, record=self.record)
            else:
                result = execute(self.request_json, self.headers, self.project, record=self.record,
                        sleep=gevent.sleep, submit=self.scenario == 'submit',
                        poll_seconds=self.poll_seconds, observe_run=self.save_run)
                self.save_journey({'ok': True, **result})
        except Exception as exc:
            self.save_journey({'ok': False, 'error': str(exc)})
            self.record('SCENARIO/' + metric, (time.perf_counter() - start) * 1000, exc)
            gevent.sleep(1)  # Avoid an unbounded error loop when a dependency is down.


    def save_run(self, result):
        self.save_journey(result, suffix='.runs.jsonl')

    def save_journey(self, result, suffix=''):
        path = os.getenv('LOADTEST_JOURNEY_LOG')
        if path and self.scenario in ('approval', 'submit'):
            target = Path(path + suffix)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('a') as f:
                f.write(json.dumps({'finished_at': datetime.now(timezone.utc).isoformat(),
                                    'scenario': self.scenario, 'poll_seconds': self.poll_seconds,
                                    **result}, ensure_ascii=False) + '\n')
