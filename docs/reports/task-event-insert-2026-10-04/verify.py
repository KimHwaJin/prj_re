"""Independent arithmetic and cohort/source checks from compressed raw evidence."""
import collections,gzip,hashlib,io,json,math,pathlib,statistics,subprocess,tarfile
R=pathlib.Path(__file__).resolve().parent;W=R.parents[2]
def read(name):return json.loads((R/name).read_text())
checks=0
def check(value,label):
 global checks
 assert value,label
 checks+=1
probe=read('projection-probe.json')
for stage, old, new in [('public_batch_new',16,10),('public_batch_replay',4,4)]:
 check(probe['before'][stage]['statements']==old and probe['after'][stage]['statements']==new, 'portable SQL budget '+stage)
 check(probe['before'][stage]['commits']==probe['after'][stage]['commits']==1,'commit boundary '+stage)
records=read('attempts.json');audit=read('source-audit.json');measurements=read('measurements.json');comparison=read('comparison.json')
source={}
for commit in (audit['before'],audit['after']):
 data=subprocess.check_output(['git','archive',commit,'src'],cwd=W)
 with tarfile.open(fileobj=io.BytesIO(data)) as tree:
  source[commit]={m.name:hashlib.sha256(tree.extractfile(m).read()).hexdigest() for m in tree if m.isfile() and m.name.endswith('.py') and '__pycache__' not in m.name}
check([p for p in audit['production_delta'] if '/test/' not in p] ==
      ['src/api_service/events/__init__.py', 'src/api_service/events/task_event_write.py', 'src/api_service/services/task_event_service.py'], 'only Run task-event write path')
for commit in (audit['before'], audit['after']):
    data = subprocess.check_output(['git', 'archive', commit, 'scripts/benchmarks/worker_e2e', 'scripts/benchmarks/executor_throughput'], cwd=W)
    with tarfile.open(fileobj=io.BytesIO(data)) as tree:
        hashes = {m.name:hashlib.sha256(tree.extractfile(m).read()).hexdigest() for m in tree
                  if m.isfile() and m.name.endswith('.py') and '__pycache__' not in m.name}
    check(hashes == audit['harness_sha256'], 'identical fixed harness')
unchanged = set(source[audit['before']]) - set(audit['source_delta'])
check(all(source[audit['before']][p] == source[audit['after']][p] for p in unchanged), 'Agent/checkpoint/claim/config unchanged')
calculated=[];users=0
for rec in records:
 raw=gzip.decompress((R/rec['raw']).read_bytes());check(hashlib.sha256(raw).hexdigest()==rec['sha256'],'raw hash '+rec['name']);x=json.loads(raw);c=x['config'];n=c['users'];users+=n;s=x['server'];d=x['database'];results=x['results'];ops=2 if c['observation_profile']=='standard' else 20;steps=4 if ops==2 else 20;models=4 if ops==2 else 23
 check(c['source_commit']==rec['source_commit'] and c['source_sha256']==source[rec['source_commit']],'pinned source '+rec['name'])
 check(rec['exit_code']==0 and x['passed'] and not x['errors'] and len(results)==n,'all attempts completed')
 check(c['delay_ms']==0 and not c['real_executor'] and c['total_capacity']==20 and c['service_pool']==10 and c['checkpoint_pool']==4 and c['event_pool']==4 and c['overflow']==0,'equal capacity/no LLM')
 check(len(s['models'])==models*n,'same model count')
 check(s['current_shared']==s['current_worker']==s['current_event_worker']==s['crud_connections_checked_out']==0 and s['peak_shared']<=20,'runtime drained')
 check(d['session_owners']==d['recovery_tasks']==d['outbox_pending']==d['inbox_pending']==0,'durable drain')
 check(all(r['state']=='DONE' and r['failure_attempts']==0 for r in d['commands']),'event commands done')
 check(all(r['state']=='DONE' for r in d['common_commands']),'common commands done')
 check(len(d['runs'])==3*n and all(r['attempt_count']==1 for r in d['runs']),'invocations one attempt')
 check(collections.Counter(r['status'] for r in d['runs'])=={'interrupted':2*n,'success':n},'invocation statuses')
 check(len({r['session_id'] for r in results})==n,'unique users sessions')
 sessions={r['session_id'] for r in results};executions=[e for e in x['mock']['executions'] if e['context']['session_id'] in sessions]
 check(len(executions)==n and all(e['status']=='SUCCEEDED' and e['operations']==ops and e['events']==(10 if ops==2 else 62) for e in executions),'measured executor fixture excludes warmup')
 check({e['execution_id'] for e in executions}=={r['execution_id'] for r in results},'execution links')
 check(not x['mock']['tasks_failed'] and x['mock']['pending']==0,'fixture drained')
 check(len({h['event_id'] for h in s['event_handlers'] if not h['error']})==n*(ops+1),'event resume count')
 for r in results:
  check([q['status'] for q in r['requests']]==[201,201,202,202,202], 'CRUD and run HTTP statuses')
  check(r['passed'] and len(r['observations'])==steps and all(o['status']=='SUCCEEDED' and not o['error'] and not o['incomplete'] for o in r['observations']),'result facts')
  check(r['report']['status']=='ready' and set(r['report']['evidence_steps'])=={o['step_id'] for o in r['observations']},'report facts')
 values=[r['seconds'] for r in results];p=s['cpu_profile'];row={'name':rec['name'],'variant':rec.get('variant','before'),'users':n,'profile':c['observation_profile'],'cpu_profile':rec['cpu_profile'],'elapsed_seconds':x['elapsed_seconds'],'flow_mean_seconds':statistics.mean(values),'flow_p95_seconds':sorted(values)[math.ceil(.95*n)-1],'cpu_seconds':s['cpu_seconds'],'sql_count':sum(r['count'] for r in s['sql']), 'snapshot_sql_count':sum(r['count'] for r in s['sql'] if 'AS agent_runs_1' in r['fingerprint'] and 'AS agent_runs_2' in r['fingerprint'])};calculated.append(row)
 saved=next(r for r in measurements if r['name']==rec['name'])
 check(all(math.isclose(row[k],saved[k],rel_tol=1e-12,abs_tol=1e-12) for k in ('elapsed_seconds','flow_mean_seconds','flow_p95_seconds','cpu_seconds','sql_count','snapshot_sql_count')),'independent aggregates')
 if rec['cpu_profile']:
  check(p is not None and p['timer']=='time.thread_time' and p['main_thread_cpu_seconds']>0 and p['offload_thread_cpu_seconds']>0 and p['export_cpu_seconds']>=0,'profile threads and export')
 else:check(p is None,'speed run without profiler')
speed=[r for r in calculated if r['name'].startswith(('before-u','after-u'))]
for row in comparison:
 n=row['users'];pair={v:[r for r in calculated if not r['cpu_profile'] and r['variant']==v and r['users']==n and r['profile']==row['profile']] for v in ('before','after')}
 check(len(pair['before'])==len(pair['after'])==(3 if n==50 and row['profile']=='standard' else 1),'repeat denominator')
 for key in ('elapsed_seconds','flow_mean_seconds','flow_p95_seconds','cpu_seconds','sql_count','snapshot_sql_count'):
  b=statistics.mean(r[key] for r in pair['before']);a=statistics.mean(r[key] for r in pair['after'])
  check(math.isclose(row['before_'+key],b) and math.isclose(row['after_'+key],a) and math.isclose(row[key+'_reduction_percent'],100*(1-a/b),abs_tol=1e-12),'comparison recalculated')
purpose_rows={q['name']:q for q in read('sql-purpose.json')}
for rec in records:
 x=json.loads(gzip.decompress((R/rec['raw']).read_bytes()));sql=x['server']['sql'];saved=purpose_rows[rec['name']]
 check(sum(saved['category_counts'].values())==sum(q['count'] for q in sql),'SQL category denominator')
 atomic=[q for q in sql if q['category'] in {'worker','event_graph'} and q['fingerprint'].startswith('WITH allocated_event_sequence AS')]
 check(sum(q['count'] for q in atomic)==saved['worker_event_graph']['counts'].get('atomic_run_event',0),'atomic write statements independently counted')
b=purpose_rows['before-u50-r1']['worker_event_graph']['counts'];a=purpose_rows['after-u50-r1']['worker_event_graph']['counts']
check(b.get('atomic_run_event',0)==0 and a['atomic_run_event']==1000,'50-user one-statement writes')
for key in ('log_pair_read','log_insert','advisory_barrier'):
 check(b[key]==a[key],'existing protections preserved '+key)
for key in ('sequence_allocation','task_event_insert','run_task_link'):
 check(b[key]==a.get(key,0)+a['atomic_run_event'],'logical event operations preserved '+key)
for variant in ('before','after'):
 for stage in ('public_batch_new','public_batch_replay'):
  check(len(probe[variant][stage]['sql'])==probe[variant][stage]['statements'],'portable probe SQL denominator')
data=subprocess.check_output(['git','archive',audit['reference_070'],'src'],cwd=W)
with tarfile.open(fileobj=io.BytesIO(data)) as tree:
 hashes={m.name:hashlib.sha256(tree.extractfile(m).read()).hexdigest() for m in tree if m.isfile() and m.name.endswith('.py') and '__pycache__' not in m.name}
check(audit['before_matches_070'] and hashes==source[audit['before']],'held071 excluded: before source equals070')
summary={'checks':checks,'trials':len(records),'user_flows':users,'standard_speed_trials':len(speed),'standard_speed_user_flows':sum(r['users'] for r in speed),'auxiliary_speed_trials':sum(not r['cpu_profile'] and r['profile']=='large20' for r in calculated),'profile_trials':sum(r['cpu_profile'] for r in calculated),'failures':sum(r['exit_code']!=0 for r in records),'passed':True}
print(json.dumps(summary,indent=2))
(R/'verification.json').write_text(json.dumps(summary,indent=2)+'\n')
