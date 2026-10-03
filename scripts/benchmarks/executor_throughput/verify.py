"""Independent raw SHA256/arithmetic checks. Does not import the analyzer."""
import argparse,gzip,hashlib,json,math
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('report',type=Path);a=p.parse_args()
rows=json.loads((a.report/'results.json').read_text());manifest=json.loads((a.report/'manifest.json').read_text());checks=0
assert len(rows)==len(manifest)
for row,entry in zip(rows,manifest):
    packed=(a.report/entry['path']).read_bytes();data=gzip.decompress(packed)
    assert hashlib.sha256(data).hexdigest()==entry['raw_sha256']
    assert hashlib.sha256(packed).hexdigest()==entry['gzip_sha256']
    raw=json.loads(data);n=raw['config']['users'];s=raw['server'];db=raw['database'];results=raw['results']
    values=sorted(r['seconds'] for r in results);rank=(len(values)-1)*.95;lower=int(rank)
    p95=values[lower]+(values[min(lower+1,len(values)-1)]-values[lower])*(rank-lower)
    expected={'mean_seconds':sum(values)/n,'p95_seconds':p95,'makespan_seconds':raw['elapsed_seconds'],
        'completed_users_per_second':n/raw['elapsed_seconds'],'api_cpu_seconds_per_user':s['cpu_seconds']/n,
        'crud_sql_per_user':sum(q['count'] for q in s['sql'])/n,
        'queue_total_per_user_ms':sum(r['queue_ms'] for r in db['runs'])/n,
        'event_handler_mean_ms':sum(h['end']-h['start'] for h in s['event_handlers'] if h['error'] is None)*1000/(n*3),
        'rss_peak_mib':max(r['rss_kib'] for r in raw['samples'])/1024,
        'db_connections_peak':max(r['db_connections'] for r in raw['samples'])}
    if not raw['config']['real_executor']:
        published={e['event_id']:e['at'] for e in raw['mock']['timeline'] if e['stage']=='event_published'}
        expected['event_wait_mean_ms']=sum(h['start']-published[h['event_id']] for h in s['event_handlers'] if h['error'] is None)*1000/(n*3)
    for key,value in expected.items():assert math.isclose(row[key],value,abs_tol=1e-8), (row['set'],key)
    assert raw['passed'] and not raw['errors'] and len(results)==n and len([h for h in s['event_handlers'] if h['error'] is None])==n*3
    checks+=len(expected)+4
receipt={'trials':len(rows),'successful_users':sum(r['users'] for r in rows),'arithmetic_and_hash_checks':checks,'passed':True}
(a.report/'independent-verification.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt))
