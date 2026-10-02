"""Independent arithmetic/hash verification without importing the analyzer."""
import argparse,gzip,hashlib,json,math
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('report',type=Path);a=p.parse_args()
results=json.loads((a.report/'results.json').read_text());manifest=json.loads((a.report/'manifest.json').read_text())
assert len(results)==len(manifest)
checks=0
for row,item in zip(results,manifest):
 path=a.report/item['path'];compressed=path.read_bytes();raw_data=gzip.decompress(compressed)
 assert hashlib.sha256(compressed).hexdigest()==item['gzip_sha256']
 assert hashlib.sha256(raw_data).hexdigest()==item['raw_sha256']
 raw=json.loads(raw_data);n=len(raw['results']);server=raw['server'];samples=raw['samples']
 values=sorted(v['seconds'] for v in raw['results'])
 expected={'mean_seconds':sum(values)/n,'p95_seconds':values[math.ceil(n*.95)-1],
  'throughput_users_per_second':n/raw['elapsed_seconds'],'cpu_seconds_per_user':server['cpu_seconds']/n,
  'sql_per_user':sum(v['count'] for v in server['sql'])/n,
  'rss_peak_mib':max(v['rss_kib'] for v in samples)/1024,
  'db_connections_peak':max(v['db_connections'] for v in samples),
  'queue_seconds_per_user':sum(v['queue_ms'] for v in raw['database']['runs'])/1000/n,
  'worker_non_model_seconds_per_user':(sum(v['end']-v['start'] for v in server['workers'])-sum(v['end']-v['start'] for v in server['models']))/n}
 for key,value in expected.items():assert abs(row[key]-value)<1e-8,(row['trial'],key)
 assert n==row['users'] and raw['passed'] and not raw['errors']
 checks+=len(expected)+4
receipt={'trials':len(results),'arithmetic_and_hash_checks':checks,'passed':True}
(a.report/'independent-verification.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps(receipt))
