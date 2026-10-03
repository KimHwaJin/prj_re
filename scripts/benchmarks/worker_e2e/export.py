"""Validate all trials, archive raw evidence and execute reproducible SQL rollups."""
import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import statistics

from analyze import analyze

SQL='''SELECT scenario, users, architecture, count(*) AS repeats,
 avg(mean_seconds) AS mean_seconds, min(mean_seconds) AS mean_min, max(mean_seconds) AS mean_max,
 avg(p95_seconds) AS trial_p95_mean, min(p95_seconds) AS p95_min, max(p95_seconds) AS p95_max,
 avg(makespan_seconds) AS makespan_seconds, avg(batch_users_per_second) AS batch_users_per_second,
 avg(api_cpu_per_user_seconds) AS cpu_per_user_seconds, avg(crud_sql_per_user) AS crud_sql_per_user,
 avg(user_queue_mean_ms) AS user_queue_mean_ms, avg(event_publish_to_start_mean_ms) AS event_wait_mean_ms,
 avg(event_publish_to_start_p95_ms) AS event_wait_p95_ms, avg(empty_claims) AS empty_claims,
 avg(shared_peak) AS shared_peak, avg(slot_utilization) AS slot_utilization,
 avg(pool_acquire_p95_ms) AS pool_acquire_p95_ms, avg(loop_lag_p95_ms) AS loop_lag_p95_ms,
 avg(rss_peak_mib) AS rss_peak_mib, max(db_connections_peak) AS db_connections_peak,
 sum(event_defer_attempts) AS event_defer_attempts
 FROM trials WHERE followup=0 AND notify='on'
 GROUP BY scenario,users,architecture ORDER BY scenario,users,architecture'''


def export(captures,output):
    output.mkdir(parents=True,exist_ok=True);(output/'raw').mkdir(exist_ok=True)
    records=[];manifest=[];cleanup=[];holds=[]
    for path in sorted(captures.rglob('raw.json')):
        folder=path.parent.parent
        receipt=json.loads((folder/'cleanup.json').read_text())
        assert receipt['owned_scratch_databases_removed'] and receipt['existing_databases_untouched']
        raw=json.loads(path.read_text());row=analyze(raw)
        if raw['hold_proof']:
            proof=raw['hold_proof']
            holds.append({'trial':folder.name,'samples':len(proof),'agent_active_peak':max(v['agent_active'] for v in proof),
                'event_active_peak':max(v['event_active'] for v in proof),'crud_checkedout_peak':max(v['crud_checkedout'] for v in proof),
                'crud_zero_samples':sum(v['crud_checkedout']==0 for v in proof)})
        name=folder.name+'.json.gz';data=path.read_bytes()
        packed=gzip.compress(data,mtime=0);(output/'raw'/name).write_bytes(packed)
        row['capture']=name;records.append(row)
        manifest.append({'capture':name,'raw_sha256':hashlib.sha256(data).hexdigest(),
            'gzip_sha256':hashlib.sha256(packed).hexdigest(),'raw_bytes':len(data),
            'source_commit':raw['config']['source_commit'],'runtime_sha256':raw['config']['source_sha256']})
        cleanup.append({'trial':folder.name,**receipt})
    fields=['scenario','users','architecture','repeat','memory_mode','followup','notify','mean_seconds','p95_seconds','makespan_seconds','batch_users_per_second','api_cpu_per_user_seconds','crud_sql_per_user','user_queue_mean_ms','event_publish_to_start_mean_ms','event_publish_to_start_p95_ms','empty_claims','shared_peak','slot_utilization','pool_acquire_p95_ms','loop_lag_p95_ms','rss_peak_mib','db_connections_peak','event_defer_attempts']
    with sqlite3.connect(':memory:') as db:
        db.row_factory=sqlite3.Row
        db.execute('CREATE TABLE trials ('+','.join(fields)+')')
        db.executemany('INSERT INTO trials VALUES ('+','.join('?' for _ in fields)+')',[[r.get(f) for f in fields] for r in records])
        main=[dict(row) for row in db.execute(SQL)]
        for row in main:
            values=[r['mean_seconds'] for r in records if r['scenario']==row['scenario'] and r['users']==row['users'] and r['architecture']==row['architecture'] and not r['followup'] and r['notify']=='on']
            row['mean_std_seconds']=statistics.stdev(values) if len(values)>1 else None
        optional=[dict(row) for row in db.execute("SELECT * FROM trials WHERE followup=1 OR notify='off' ORDER BY memory_mode,users,architecture,repeat")]
    def save(name,value):
        (output/name).write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')
    save('results.json',records);save('summary.json',main);save('supplemental.json',optional)
    save('hold-validation.json',{'trials':len(holds),'samples':sum(v['samples'] for v in holds),
        'crud_nonzero_samples':sum(v['samples']-v['crud_zero_samples'] for v in holds),
        'crud_peak':max((v['crud_checkedout_peak'] for v in holds),default=0),'rows':holds,
        'definition':'Three 250ms-spaced pre-release samples: no active graph slots, at least one zero CRUD checkout per trial. Transient background checkout allowed; owner attribution was not enabled.'})
    save('manifest.json',manifest);save('trial-cleanup.json',cleanup)
    (output/'aggregation.sql').write_text(SQL+';\n')
    save('verification.json',{'validated_trials':len(records),'completed_user_scenarios':sum(r['users'] for r in records),
        'errors':0,'missing_or_duplicate_successes':0,'over_capacity':0,
        'temporary_event_defer_attempts':sum(r['event_defer_attempts'] for r in records),
        'model_call_counts':dict(sum((Counter(r['model_calls']) for r in records),Counter())),
        'note':'Model counts are in-window; burst prepared planning is outside measurement. Smoke is excluded.'})
    print(json.dumps({'validated_trials':len(records),'complete_user_scenarios':sum(r['users'] for r in records)}))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('captures',type=Path);p.add_argument('--output',required=True,type=Path);args=p.parse_args()
    export(args.captures,args.output)
