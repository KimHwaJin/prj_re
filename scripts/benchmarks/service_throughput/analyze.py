"""Aggregate guarded current-service traces; queue/model/worker intervals stay distinct."""
import argparse,gzip,hashlib,json,math,statistics
from collections import Counter
from pathlib import Path

def p95(values):
    values=sorted(values)
    return values[max(0,math.ceil(len(values)*.95)-1)] if values else 0

def analyze(raw):
    cfg=raw['config'];n=cfg['users'];server=raw['server'];db=raw['database'];flow=cfg['scenario']=='flow'
    assert raw['passed'] and not raw['errors'] and len(raw['results'])==n
    assert db['session_owners']==db['recovery_tasks']==0
    if 'crud_connections_checked_out' in server:assert server['crud_connections_checked_out']==0
    assert len({r['session_id'] for r in raw['results']})==n
    assert all(r['passed'] and all(q['status']<400 for q in r['requests']) for r in raw['results'])
    by_public={}
    for item in db['runs']:by_public.setdefault(item['public_run_id'],[]).append(item)
    if flow:
        assert len(by_public)==n and all(len(v)==3 for v in by_public.values())
        assert len(server['workers'])==3*n and len(server['models'])==n and server['peak_worker']<=cfg['concurrency']
        assert all(r['attempt_count']==1 and r['status'] in ('interrupted','success') for r in db['runs'])
        assert Counter(r['status'] for r in db['runs'])=={'interrupted':2*n,'success':n}
        assert len({r['run_id'] for r in server['workers']})==3*n
        assert {r['run_id'] for r in server['workers']}=={r['run_id'] for r in db['runs']}
        inv_to_public={r['run_id']:r['public_run_id'] for r in db['runs']}
        assert all(m['run_id'] in inv_to_public for m in server['models'])
        assert len({m['run_id'] for m in server['models']}) == n
        assert {inv_to_public[m['run_id']] for m in server['models']} == set(by_public)
        # Sweep exact intervals instead of inferring occupancy from samples.
        sweep=sorted([(r['start'],1) for r in server['workers']]+[(r['end'],-1) for r in server['workers']],key=lambda v:(v[0],v[1]))
        active=0;peak=0
        for _,delta in sweep:active+=delta;peak=max(active,peak)
        assert active==0 and peak==server['peak_worker']
    times=[r['seconds'] for r in raw['results']];requests=[q['ms'] for r in raw['results'] for q in r['requests']]
    model=sum(m['end']-m['start'] for m in server['models'])/n
    worker=sum(w['end']-w['start'] for w in server['workers'])/n
    queue=sum(r['queue_ms'] for r in db['runs'])/1000/n
    internal=worker-model
    assert internal>=-.001
    result={**cfg,'passed':True,'completed_users':n,'batch_seconds':raw['elapsed_seconds'],'throughput_users_per_second':n/raw['elapsed_seconds'],
      'mean_seconds':statistics.mean(times),'p95_seconds':p95(times),'queue_seconds_per_user':queue,'mock_model_seconds_per_user':model,
      'worker_non_model_seconds_per_user':internal,'client_delivery_and_other_seconds_per_user':statistics.mean(times)-queue-worker,
      'request_p95_ms':p95(requests),'cpu_seconds_per_user':server['cpu_seconds']/n,
      'peak_worker':server['peak_worker'],'pool_acquire_p95_ms':p95(server['acquires']),
      'loop_lag_p95_ms':p95(server['loop_lag']),'loop_lag_max_ms':max(server['loop_lag'],default=0),
      'rss_peak_mib':max(s['rss_kib'] for s in raw['samples'])/1024,
      'db_connections_peak':max(s['db_connections'] for s in raw['samples']),
      'db_lock_waiters_peak':max(s['db_lock_waiters'] for s in raw['samples']),
      'db_idle_transactions_peak':max(s['db_idle_transactions'] for s in raw['samples']),
      'sql_per_user':sum(s['count'] for s in server['sql'])/n,
      'sql_by_category_per_user':{k:sum(s['count'] for s in server['sql'] if s['category']==k)/n for k in {s['category'] for s in server['sql']}},
      'worker_advisory_locks_per_user':sum(s['count'] for s in server['sql'] if s['category']=='worker' and 'pg_advisory_xact_lock' in s['fingerprint'])/n}
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('inputs',nargs='+',type=Path);p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=True);records=[];manifest=[]
    for root in a.inputs:
        for path in sorted(root.glob('*/raw.json')):
            raw=json.loads(path.read_text());row=analyze(raw);row['measurement_set']=root.name;row['trial']=path.parent.name;records.append(row)
            target=a.output/'raw'/root.name/(path.parent.name+'.json.gz');target.parent.mkdir(parents=True,exist_ok=True)
            data=path.read_bytes()
            with target.open('wb') as stream:
                with gzip.GzipFile(fileobj=stream,mode='wb',mtime=0) as writer:writer.write(data)
            manifest.append({'path':str(target.relative_to(a.output)),'raw_sha256':hashlib.sha256(data).hexdigest(),'gzip_sha256':hashlib.sha256(target.read_bytes()).hexdigest()})
    assert records
    (a.output/'results.json').write_text(json.dumps(records,ensure_ascii=False,indent=2)+'\n')
    (a.output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({'trials':len(records),'users':sum(r['completed_users'] for r in records),'all_passed':True}))
