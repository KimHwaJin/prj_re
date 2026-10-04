"""Independent arithmetic and cohort/source checks from compressed raw evidence."""
import collections,gzip,hashlib,io,json,math,pathlib,statistics,subprocess,tarfile
R=pathlib.Path(__file__).resolve().parent;W=R.parents[2]
def read(name):return json.loads((R/name).read_text())
checks=0
def check(value,label):
 global checks
 assert value,label
 checks+=1
records=read('attempts.json');audit=read('source-audit.json');measurements=read('measurements.json');comparison=read('comparison.json')
source={}
for commit in (audit['before'],audit['after']):
 data=subprocess.check_output(['git','archive',commit,'src'],cwd=W)
 with tarfile.open(fileobj=io.BytesIO(data)) as tree:
  source[commit]={m.name:hashlib.sha256(tree.extractfile(m).read()).hexdigest() for m in tree if m.isfile() and m.name.endswith('.py') and '__pycache__' not in m.name}
check([p for p in audit['production_delta'] if '/test/' not in p] ==
      ['src/api_service/runs/execution.py', 'src/api_service/runs/projection.py'], 'only locked Run state reuse')
for commit in (audit['before'], audit['after']):
    data = subprocess.check_output(['git', 'archive', commit, 'scripts/benchmarks/worker_e2e', 'scripts/benchmarks/executor_throughput'], cwd=W)
    with tarfile.open(fileobj=io.BytesIO(data)) as tree:
        hashes = {m.name:hashlib.sha256(tree.extractfile(m).read()).hexdigest() for m in tree
                  if m.isfile() and m.name.endswith('.py') and '__pycache__' not in m.name}
    check(hashes == audit['harness_sha256'], 'identical fixed harness')
unchanged = set(source[audit['before']]) - set(audit['source_delta'])
check(all(source[audit['before']][p] == source[audit['after']][p] for p in unchanged), 'Agent/checkpoint/claim/config unchanged')
calculated=[];users=0
for rec in records+read('diagnostics.json'):
 raw=gzip.decompress((R/rec['raw']).read_bytes());check(hashlib.sha256(raw).hexdigest()==rec['sha256'],'raw hash '+rec['name']);x=json.loads(raw);c=x['config'];n=c['users'];users+=n;s=x['server'];d=x['database'];results=x['results'];ops=2 if c['observation_profile']=='standard' else 20;steps=4 if ops==2 else 20;models=4 if ops==2 else 23
 check(c['source_commit']==rec['source_commit'] and c['source_sha256']==source[subprocess.check_output(['git','rev-parse',rec['source_commit']],cwd=W).decode().strip()],'pinned source '+rec['name'])
 check(rec.get('exit_code',0)==0 and x['passed'] and not x['errors'] and len(results)==n,'all attempts completed')
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
 values=[r['seconds'] for r in results];p=s['cpu_profile'];row={'name':rec['name'],'variant':rec.get('variant','before'),'users':n,'profile':c['observation_profile'],'cpu_profile':rec['cpu_profile'],'elapsed_seconds':x['elapsed_seconds'],'flow_mean_seconds':statistics.mean(values),'flow_p95_seconds':sorted(values)[math.ceil(.95*n)-1],'cpu_seconds':s['cpu_seconds'],'sql_count':sum(r['count'] for r in s['sql'])}
 if rec.get('diagnostic_only'):continue
 calculated.append(row)
 saved=next(r for r in measurements if r['name']==rec['name'])
 check(all(math.isclose(row[k],saved[k],rel_tol=1e-12,abs_tol=1e-12) for k in ('elapsed_seconds','flow_mean_seconds','flow_p95_seconds','cpu_seconds','sql_count')),'independent aggregates')
 if rec['cpu_profile']:
  check(p is not None and p['timer']=='time.thread_time' and p['main_thread_cpu_seconds']>0 and p['offload_thread_cpu_seconds']>0 and p['export_cpu_seconds']>=0,'profile threads and export')
 else:check(p is None,'speed run without profiler')
 if p is not None:
  groups=collections.defaultdict(float)
  for thread in ('main','offload'):
   for q in p[thread]:
    file=q['file'];tag='sqlalchemy' if '/sqlalchemy/' in file else 'service' if '/src/' in file else 'harness' if '/scripts/benchmarks/' in file else 'other';groups[thread+':'+tag]+=q['self_cpu_seconds']
  saved_profile=next(q for q in read('profiles.json') if q['name']==rec['name'])
  check(all(math.isclose(value,saved_profile['exclusive_groups'][key],rel_tol=1e-12) for key,value in groups.items()),'exclusive profiler groups recomputed')
speed=[r for r in calculated if r['name'].startswith(('before-u','after-u'))]
for row in comparison:
 n=row['users'];pair={v:[r for r in calculated if not r['cpu_profile'] and r['variant']==v and r['users']==n and r['profile']==row['profile']] for v in ('before','after')}
 check(len(pair['before'])==len(pair['after'])==(3 if n==50 and row['profile']=='standard' else 1),'repeat denominator')
 for key in ('elapsed_seconds','flow_mean_seconds','flow_p95_seconds','cpu_seconds','sql_count'):
  b=statistics.mean(r[key] for r in pair['before']);a=statistics.mean(r[key] for r in pair['after'])
  check(math.isclose(row['before_'+key],b) and math.isclose(row['after_'+key],a) and math.isclose(row[key+'_reduction_percent'],100*(1-a/b),abs_tol=1e-12),'comparison recalculated')
purpose_rows={q['name']:q for q in read('sql-purpose.json')}
for rec in records:
 x=json.loads(gzip.decompress((R/rec['raw']).read_bytes()));saved=purpose_rows[rec['name']]
 check(sum(saved['categories'].values())==sum(q['count'] for q in x['server']['sql']),'SQL category denominator')
 check(x['config']['query_audit'] is False,'speed/profiler trials without query tracing')
b=purpose_rows['before-u50-r1']['worker_event_graph'];a=purpose_rows['after-u50-r1']['worker_event_graph']
for key in ('refresh_run','refresh_task','locked_run','locked_task'):
 check(b[key]-a.get(key,0)==150,'150 duplicate reads removed '+key)
for key in ('session_read','user_read','project_read','message_duplicate_read','advisory_barrier','message_insert','log_insert','event_insert','log_pair_read'):
 check(b[key]==a[key],'protective or durable SQL preserved '+key)
for rec in read('diagnostics.json'):
 x=json.loads(gzip.decompress((R/rec['raw']).read_bytes()));n=rec['users'];qs=x['server']['query_audit_sql'];groups=collections.defaultdict(list)
 check(x['config']['query_audit'] is True and x['config']['observation_profile']=='standard','separate traced diagnostic')
 check(rec['cpu_profile']==(n==10) and (x['server']['cpu_profile'] is not None)==rec['cpu_profile'],'diagnostic profiler configuration')
 check(all(len(q['parameter_sha256'])==64 and 'parameters' not in q for q in qs),'no parameter values in query audit')
 for q in qs:
  f=q['fingerprint'];origin=q['scope'][0]['name']
  if origin not in ('prepare','executor_projection') or not f.startswith('SELECT'):continue
  if not ((' FROM agent_runs WHERE' in f and f.startswith('SELECT agent_runs.run_id,')) or (' FROM tasks WHERE' in f and f.startswith('SELECT tasks.task_id,'))):continue
  groups[(origin,q['scope'][0]['call_id'],q['transaction_id'],f.removesuffix(' FOR UPDATE'),q['parameter_sha256'])].append(q)
 repeated={origin:sum(len(q)-1 for key,q in groups.items() if key[0]==origin) for origin in ('prepare','executor_projection')}
 check(repeated=={key:(6*n if rec['variant']=='before' else 0) for key in repeated},'same transaction and binding duplicates before/after')
 saved=next(v for v in read('query-audit.json') if v['name']==rec['name'])
 check(saved['redundant_same_transaction_same_binding_reads']==repeated and saved['all_audited_sql']==len(qs),'diagnostic aggregates recomputed')
 check(saved['lock_session_reads']==28*n and saved['message_duplicate_queries']==11*n,'permission and message dedup kept')
 check(sum(v['name']=='prepare' for v in x['server']['query_audit_calls'])==3*n and sum(v['name']=='executor_projection' for v in x['server']['query_audit_calls'])==3*n,'same logical invocation count')
data=subprocess.check_output(['git','archive',audit['reference_070'],'src'],cwd=W)
with tarfile.open(fileobj=io.BytesIO(data)) as tree:
 hashes={m.name:hashlib.sha256(tree.extractfile(m).read()).hexdigest() for m in tree if m.isfile() and m.name.endswith('.py') and '__pycache__' not in m.name}
check(audit['before_matches_070'] and hashes==source[audit['before']],'held071 and072 excluded: before source equals070')
summary={'checks':checks,'trials':len(records),'user_flows':sum(q['users'] for q in records),'diagnostic_trials':len(read('diagnostics.json')),'diagnostic_flows':sum(q['users'] for q in read('diagnostics.json')),'total_flows':users,'standard_speed_trials':len(speed),'standard_speed_user_flows':sum(r['users'] for r in speed),'auxiliary_speed_trials':sum(not r['cpu_profile'] and r['profile']=='large20' for r in calculated),'profile_trials':sum(r['cpu_profile'] for r in calculated),'failures':sum(r['exit_code']!=0 for r in records),'passed':True}
print(json.dumps(summary,indent=2))
(R/'verification.json').write_text(json.dumps(summary,indent=2)+'\n')
