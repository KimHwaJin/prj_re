#!/usr/bin/env python3
"""Measure bounded Locust plateaus or soaks with saved raw evidence."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import threading
import time

import httpx

ROOT = Path(__file__).resolve().parents[2]


def now():
    return datetime.now(timezone.utc).isoformat()


def command(*args):
    return subprocess.check_output(args, cwd=ROOT, text=True, timeout=15).strip()


def database_snapshot():
    sql = """SELECT json_build_object(
      'users',(SELECT count(*) FROM users),
      'projects',(SELECT count(*) FROM projects),
      'sessions',(SELECT count(*) FROM sessions),
      'active_projects',(SELECT count(*) FROM projects WHERE delete_yn='N'),
      'active_sessions',(SELECT count(*) FROM sessions WHERE delete_yn='N'),
      'run_status_counts',(SELECT json_object_agg(status,n) FROM (SELECT status,count(*) n FROM agent_runs GROUP BY status) t),
      'retried_runs',(SELECT count(*) FROM agent_runs WHERE attempt_count>1),
      'agent_runs',(SELECT count(*) FROM agent_runs),
      'tasks',(SELECT count(*) FROM tasks),
      'db_size_bytes',pg_database_size(current_database()),
      'deadlocks',(SELECT deadlocks FROM pg_stat_database WHERE datname=current_database()),
      'temp_bytes',(SELECT temp_bytes FROM pg_stat_database WHERE datname=current_database()),
      'xact_commit',(SELECT xact_commit FROM pg_stat_database WHERE datname=current_database()),
      'xact_rollback',(SELECT xact_rollback FROM pg_stat_database WHERE datname=current_database()),
      'blks_read',(SELECT blks_read FROM pg_stat_database WHERE datname=current_database()),
      'blks_hit',(SELECT blks_hit FROM pg_stat_database WHERE datname=current_database())
    )"""
    return json.loads(command('docker','exec','dtest-agent-loadtest-postgres-1','psql','-U','dtest','-d','chat_app','-Atc',sql))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', choices=['crud','crud_mixed','approval'], default='crud')
    parser.add_argument('--output', required=True)
    parser.add_argument('--users', default='1,5,10,25,50,75,100')
    parser.add_argument('--warmup', type=float, default=15)
    parser.add_argument('--seconds', type=float, default=60)
    parser.add_argument('--poll-seconds', type=float, default=0.25)
    args=parser.parse_args()
    users=[int(n) for n in args.users.split(',')]
    if not users or min(users)<1 or max(users)>100 or args.seconds<=0 or args.warmup<0 or args.poll_seconds<=0:
        parser.error('Require users 1..100 and positive measurement duration')
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    if (out/'metadata.json').exists():
        parser.error('Output already contains a test run; choose a new directory to preserve evidence')
    context={'phase':'setup','users':0}
    stop=threading.Event()

    def monitor():
        with (out/'resources.jsonl').open('w') as f:
            while not stop.is_set():
                sample={'at':now(),**context}
                try:
                    sample['containers']=[json.loads(line) for line in command('docker','stats','--no-stream','--format','{{json .}}').splitlines()]
                    sql="SELECT json_build_object('connections',count(*),'active',count(*) FILTER(WHERE state='active'),'idle_in_transaction',count(*) FILTER(WHERE state='idle in transaction'),'lock_waits',count(*) FILTER(WHERE wait_event_type='Lock')) FROM pg_stat_activity WHERE datname='chat_app' AND pid<>pg_backend_pid()"
                    sample['database']=json.loads(command('docker','exec','dtest-agent-loadtest-postgres-1','psql','-U','dtest','-d','chat_app','-Atc',sql))
                    sample['all_databases']=json.loads(command('docker','exec','dtest-agent-loadtest-postgres-1','psql','-U','dtest','-d','chat_app','-Atc',
                        "SELECT COALESCE(json_object_agg(datname,counts),'{}'::json) FROM (SELECT datname,json_build_object('connections',count(*),'active',count(*) FILTER(WHERE state='active'),'locks',count(*) FILTER(WHERE wait_event_type='Lock')) counts FROM pg_stat_activity WHERE datname IS NOT NULL AND pid<>pg_backend_pid() GROUP BY datname) s"))
                    sample['runs']=json.loads(command('docker','exec','dtest-agent-loadtest-postgres-1','psql','-U','dtest','-d','chat_app','-Atc',
                        "SELECT json_build_object('pending',count(*) FILTER(WHERE status='pending'),'running',count(*) FILTER(WHERE status='running'),'errors',count(*) FILTER(WHERE status='error'),'timeouts',count(*) FILTER(WHERE status='timeout'),'retried',count(*) FILTER(WHERE attempt_count>1)) FROM agent_runs"))
                except Exception as exc:
                    sample['error']=str(exc)
                f.write(json.dumps(sample)+'\n');f.flush()
                stop.wait(3)

    def save(name,data):
        (out/name).write_text(json.dumps(data,ensure_ascii=False,indent=2))

    with httpx.Client(base_url='http://127.0.0.1:18089',timeout=15) as client:
        def get(path):
            r=client.get(path);r.raise_for_status();return r.json()
        state=get('/stats/requests')
        if state['state'] not in ('ready','stopped') or state['user_count']:
            raise RuntimeError('Locust UI is already running; stop it before this test')
        metadata={'started_at':now(),'users':users,'warmup_seconds':args.warmup,'measurement_seconds':args.seconds,
                  'spawn_rate':10,'target':'http://api:8000','scenario':args.scenario,'poll_seconds':args.poll_seconds,
                  'git_head':command('git','rev-parse','HEAD'),'git_branch':command('git','branch','--show-current'),
                  'docker':command('docker','info','--format','{{.NCPU}} CPUs {{.MemTotal}} bytes'),
                  'api_command':json.loads(command('docker','inspect','dtest-agent-loadtest-api-1','--format','{{json .Config.Cmd}}')),
                  'before_database':database_snapshot(),
                  'before_mock_executor':httpx.get('http://127.0.0.1:18081/health').json()}
        save('metadata.json',metadata)
        worker=threading.Thread(target=monitor,daemon=True);worker.start()
        stages=[]
        try:
            for n in users:
                context.update(phase='ramp',users=n)
                r=client.post('/swarm',data={'user_count':n,'spawn_rate':10,'host':'http://api:8000','scenario':args.scenario,'poll_seconds':args.poll_seconds})
                r.raise_for_status()
                deadline=time.monotonic()+60
                while get('/stats/requests')['user_count'] != n:
                    if time.monotonic()>deadline:raise TimeoutError(f'Failed to reach {n} users')
                    time.sleep(0.5)
                context['phase']='warmup'
                time.sleep(args.warmup)
                context['phase']='measure'
                client.get('/stats/reset').raise_for_status()
                start=time.monotonic(); started=now()
                samples=[]
                next_progress=60
                while time.monotonic()-start < args.seconds:
                    time.sleep(min(5,max(0,args.seconds-(time.monotonic()-start))))
                    sample={'at':now(),'elapsed':time.monotonic()-start,'data':get('/stats/requests')}
                    samples.append(sample)
                    with (out/f'samples-{n:03}.jsonl').open('a') as f:
                        f.write(json.dumps(sample)+'\n')
                    if sample['elapsed'] >= next_progress:
                        rows=[r for r in sample['data']['stats'] if r['method'] in ('GET','POST','PATCH','DELETE')]
                        print(json.dumps({'progress_seconds':round(sample['elapsed']), 'users':n,
                            'scenario':args.scenario,'http_requests':sum(r['num_requests'] for r in rows),
                            'http_failures':sum(r['num_failures'] for r in rows)}),flush=True)
                        next_progress+=60
                elapsed=time.monotonic()-start
                stats=samples[-1]['data']
                stage={'users':n,'started_at':started,'finished_at':now(),'elapsed_seconds':elapsed,
                       'stats':stats,'samples':samples,'database':database_snapshot()}
                stages.append(stage);save(f'stage-{n:03}.json',stage);save('stages.json',stages)
                rows=[row for row in stats['stats'] if row['method'] in ('GET','POST','PATCH','DELETE') and row['num_requests']]
                print(json.dumps({'users':n,'requests':sum(row['num_requests'] for row in rows),
                    'failures':sum(row['num_failures'] for row in rows),
                    'rps':round(sum(row['num_requests'] for row in rows)/elapsed,2),
                    'p95_ms':{row['name']:row['response_time_percentile_0.95'] for row in rows}},ensure_ascii=False),flush=True)
                if stats['user_count']!=n:raise RuntimeError(f'User count dropped at stage {n}')
        finally:
            context['phase']='stop'
            # Locust's stop endpoint can wait for the configured graceful drain.
            client.get('/stop', timeout=200).raise_for_status()
            deadline = time.monotonic() + 210
            while True:
                status = get('/stats/requests')
                if status['state'] == 'stopped' and status['user_count'] == 0:
                    break
                if time.monotonic() > deadline:
                    raise TimeoutError('Locust did not stop within 210 seconds')
                time.sleep(0.5)
            save('after-stop-stats.json',status)
            stop.set();worker.join(timeout=20)
            metadata.update(finished_at=now(),after_database=database_snapshot(),
                after_mock_executor=httpx.get('http://127.0.0.1:18081/health').json(),final_locust=get('/stats/requests')['state'])
            save('metadata.json',metadata)


if __name__=='__main__':main()
