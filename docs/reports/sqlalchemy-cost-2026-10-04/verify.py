"""Independent raw/cohort/source arithmetic checks; no services required."""
import collections,gzip,hashlib,io,json,math,subprocess,tarfile
from pathlib import Path
R=Path(__file__).resolve().parent;W=R.parents[2]
checks=0
def check(value,label):
 global checks
 assert value,label
 checks+=1
def read(name):return json.loads((R/name).read_text())
audit=read('source-audit.json');records=read('attempts.json');saved=read('measurements.json')
check(audit['production_delta']==[],'diagnosis contains no production changes')
check(audit['harness_delta']==['scripts/benchmarks/worker_e2e/cpu_profile.py'],'only caller export changed in trial source')
check(subprocess.check_output(['git','diff','--name-only',audit['baseline'],audit['diagnostic_source'],'--','src'],cwd=W).decode().strip()=='','production source unchanged')
data=subprocess.check_output(['git','archive',audit['diagnostic_source'],'src'],cwd=W)
with tarfile.open(fileobj=io.BytesIO(data)) as tree:
 hashes={m.name:hashlib.sha256(tree.extractfile(m).read()).hexdigest() for m in tree if m.isfile() and m.name.endswith('.py') and '__pycache__' not in m.name}
check(len(records)==4 and sum(r['users'] for r in records)==22,'four attempts / twenty-two flows')
check([r['name'] for r in records]==['standard1-profile','standard10-profile','large1-profile','standard10-control'],'no hidden attempts')
for rec in records:
 raw=gzip.decompress((R/rec['raw']).read_bytes());check(hashlib.sha256(raw).hexdigest()==rec['sha256'],'raw hash');x=json.loads(raw);c=x['config'];s=x['server'];d=x['database'];n=rec['users'];ops=20 if rec['observation_profile']=='large20' else 2;tools=20 if ops==20 else 4;models=23 if ops==20 else 4
 check(rec['exit_code']==0 and x['passed'] and x['errors']==[] and len(x['results'])==n,'completed all attempts')
 check(c['source_commit']==audit['diagnostic_source'] and c['source_sha256']==hashes,'pinned production source')
 check(c['delay_ms']==0 and not c['real_executor'] and c['total_capacity']==20 and c['service_pool']==10 and c['checkpoint_pool']==4 and c['event_pool']==4 and c['overflow']==0 and not c['query_audit'],'fixed service-only conditions')
 check((s['cpu_profile'] is not None)==rec['cpu_profile'],'profiler mode')
 check(len(s['models'])==n*models,'model calls unchanged')
 check(s['current_shared']==s['current_worker']==s['current_event_worker']==s['crud_connections_checked_out']==0 and s['peak_shared']<=20,'runtime drain/capacity')
 check(d['session_owners']==d['recovery_tasks']==d['outbox_pending']==d['inbox_pending']==0,'durable drain')
 check(all(q['state']=='DONE' and q['failure_attempts']==0 for q in d['commands']) and all(q['state']=='DONE' for q in d['common_commands']),'commands drained')
 check(len(d['runs'])==3*n and all(q['attempt_count']==1 for q in d['runs']) and collections.Counter(q['status'] for q in d['runs'])=={'interrupted':2*n,'success':n},'run invocation statuses')
 for q in x['results']:
  check(q['passed'] and [h['status'] for h in q['requests']]==[201,201,202,202,202],'CRUD/HITL HTTP statuses')
  check(len(q['observations'])==tools and all(o['status']=='SUCCEEDED' and not o['error'] and not o['incomplete'] for o in q['observations']),'execution result facts')
  check(q['report']['status']=='ready' and set(q['report']['evidence_steps'])=={o['step_id'] for o in q['observations']},'report evidence')
 sessions={q['session_id'] for q in x['results']};executions=[e for e in x['mock']['executions'] if e['context']['session_id'] in sessions]
 check(len(executions)==n and all(e['status']=='SUCCEEDED' and e['operations']==ops and e['events']==(62 if ops==20 else 10) for e in executions),'fixture excludes warmup')
 check(not x['mock']['tasks_failed'] and x['mock']['pending']==0,'fixture drained')
 v=next(q for q in saved if q['name']==rec['name']);check(v['elapsed_seconds_diagnostic']==x['elapsed_seconds'] and v['process_cpu_seconds']==s['cpu_seconds'],'saved clocks')
 check(v['task_link_selects']==sum(q['count'] for q in s['sql'] if 'FROM tasks JOIN agent_runs' in q['fingerprint'])==n*(114 if ops==20 else 23),'Task link SELECT counts')
 check(v['log_event_pair_selects']==sum(q['count'] for q in s['sql'] if 'FROM agent_run_logs LEFT OUTER JOIN task_events' in q['fingerprint'])==n*(206 if ops==20 else 41),'log/event SELECT counts')
 check(v['sql_count']==sum(q['count'] for q in s['sql']),'SQL denominator')
 p=s['cpu_profile']
 if not p:continue
 groups=collections.defaultdict(float)
 for q in p['main']:
  file=q['file']
  if '/sqlalchemy/' not in file:continue
  rel=file.split('/sqlalchemy/')[-1]
  if rel=='sql/compiler.py':group='sql_compiler'
  elif rel.startswith('sql/'):group='sql_expression_cache'
  elif rel=='orm/loading.py':group='orm_row_loading'
  elif rel in ('orm/session.py','orm/context.py','orm/state_changes.py'):group='orm_session_context_transaction'
  elif rel.startswith('orm/'):group='orm_state_flush_other'
  elif rel.split('/')[0] in ('engine','dialects','pool','ext','util','event'):group='db_execution_result_bridge'
  else:group='sqlalchemy_other'
  groups[group]+=q['self_cpu_seconds']
 check(set(groups)==set(v['sqlalchemy_exclusive_groups']),'exclusive partition labels')
 for k,value in groups.items():check(math.isclose(value,v['sqlalchemy_exclusive_groups'][k],rel_tol=1e-12),'independent category sum')
 check(math.isclose(sum(groups.values()),v['sqlalchemy_self_cpu_seconds'],rel_tol=1e-12),'category denominator')
 check(p['timer']=='time.thread_time','CPU clock excludes socket wall waits')
 for thread in ('main','offload'):
  check(0<=sum(q['self_cpu_seconds'] for q in p[thread])<=p[thread+'_thread_cpu_seconds'],'profile CPU bounded by measured thread')
  for q in p[thread]:
   check(set(q)=={'file','line','function','primitive_calls','calls','self_cpu_seconds','cumulative_cpu_seconds','callers'},'profile has metadata only')
   check(sum(e['calls'] for e in q['callers'])<=q['calls'],'caller count bound')
   for e in q['callers']:check(set(e)=={'file','line','function','calls','primitive_calls','self_cpu_seconds','cumulative_cpu_seconds'} and e['calls']>=e['primitive_calls']>=0,'caller edge metadata/counts')
 check(v['main_thread_cpu_seconds']==p['main_thread_cpu_seconds'],'main CPU reference')
 if rec['name']=='standard10-profile':
  select=next(q for q in p['main'] if q['file'].endswith('/sqlalchemy/sql/_selectable_constructors.py') and q['function']=='select')
  by_name={e['function']:e['calls'] for e in select['callers'] if '/src/' in e['file']}
  check(by_name['create']==417 and by_name['get_active']==270 and by_name['attach_graph_task_for_run']==233,'synchronous builders reached from actual service callers')
  compiler=[q for q in p['main'] if q['file'].endswith('/sqlalchemy/sql/compiler.py') and q['function']=='__init__']
  check(len(compiler)==2 and all(q['calls']==200 for q in compiler),'200 SQL compiler instances; two frames on same constructor path')
  check(all(q['function']!='visit_select' for q in p['main'] if '/sqlalchemy/' in q['file']),'no SELECT text recompilation in measured warm interval')
check('3 passed' in (R/'regression.log').read_text(),'diagnostic tests passed')
cleanup=read('cleanup.json');check(cleanup['preserved_original_container_count']==cleanup['original_container_count']==18 and not cleanup['missing_original_containers'] and not cleanup['owned_resources_remaining'],'owned resources removed/original services preserved')
result={'passed':True,'checks':checks,'attempts':4,'profile_attempts':3,'unprofiled_control_attempts':1,'completed_flows':22,'failed_attempts':0,'production_changed':False,'speed_improvement_claimed':False}
print(json.dumps(result,indent=2));(R/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
