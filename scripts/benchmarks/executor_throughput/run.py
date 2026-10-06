"""Actual API/PG/Streams/Event Worker/SSE with loopback HTTP Executor fixture."""
import argparse,asyncio,json,os,signal,socket,subprocess,sys,tempfile,time
from pathlib import Path
from uuid import uuid4,UUID
import httpx,psycopg,yaml
from psycopg import sql
from sqlalchemy.engine import make_url
ROOT=Path(__file__).resolve().parents[3]
# This runner/capture schema is the 057 two-dispatcher baseline. The common
# scheduler needs shared-peak measurement and an equal-total-capacity control.
# Refuse instead of publishing a comparison with misleading EW limits.
if (ROOT/'src/dtest/application/runs/commands/claim.py').exists():
 raise SystemExit('057 benchmark runner targets the previous two-dispatcher architecture. Use the recorded baseline source; stage 5 will add the shared-capacity runner. Current command-worker functional tests are available in test_agent_commands_postgres.py.')
p=argparse.ArgumentParser();p.add_argument('--settings-file',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
p.add_argument('--users',type=int,nargs='+',default=[1,10,30,50]);p.add_argument('--concurrency',type=int,nargs='+',default=[4,16,32])
p.add_argument('--delay-ms',type=int,default=5000);p.add_argument('--repeat',type=int,default=1)
p.add_argument('--event-concurrency',type=int,default=4);p.add_argument('--event-pool',type=int,default=4)
p.add_argument('--event-poll',type=float,default=.2);p.add_argument('--event-idle',type=float,default=2)
p.add_argument('--executor-delay-ms',type=int,default=0);p.add_argument('--hold-seconds',type=float,default=0)
p.add_argument('--real-executor',action='store_true');
p.add_argument('--scenario',choices=['executor'],default='executor');p.add_argument('--pool',type=int,default=10)
p.add_argument('--checkpoint-pool',type=int,default=4);p.add_argument('--sse-seconds',type=float,default=.5)
p.add_argument('--cancel-seconds',type=float,default=.25);p.add_argument('--claim-seconds',type=float,default=.25)
p.add_argument('--hold-owners',action='store_true',help='Diagnostic live CRUD checkout owners during an Executor hold')
p.add_argument('--cache-size',type=int,default=100);a=p.parse_args()
assert all(1<=n<=50 for n in a.users) and all(1<=n<=64 for n in a.concurrency)
assert a.event_concurrency>=1 and a.event_pool>=2 and a.event_idle>=a.event_poll>0
assert not a.real_executor or (a.users==[1] and a.executor_delay_ms==0 and a.hold_seconds==0)
assert a.executor_delay_ms>=0 and a.hold_seconds>=0
assert a.delay_ms in (0,5000) and a.repeat>=1 and a.pool>=1
assert not a.output.exists(),'Use a fresh output directory'
a.output.mkdir(parents=True)
private=json.loads(a.settings_file.read_text());local=make_url(private.get('database_url',private.get('DATABASE_URL')))
assert local.host in ('127.0.0.1','localhost') and local.database=='agentic_runtime_test','Use the guarded existing local test connection only'
# Never migrate/reset an existing database. Own two fresh, collision-checked databases.
stem='service_perf_'+uuid4().hex[:10];crud=local.set(database=stem);checkpoint=local.set(drivername='postgresql',database=stem+'_cp')
admin=local.set(drivername='postgresql',database='postgres').render_as_string(hide_password=False)
DSN=crud.set(drivername='postgresql').render_as_string(hide_password=False);CP=checkpoint.render_as_string(hide_password=False)
def private_json(path,value):
 descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
 with os.fdopen(descriptor,'w') as f:os.fchmod(f.fileno(),0o600);json.dump(value,f)
def port():
 with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
owned_databases=[]
def setup_databases():
 with psycopg.connect(admin,autocommit=True) as db:
  for name in (stem,stem+'_cp'):
   assert not db.execute('SELECT 1 FROM pg_database WHERE datname=%s',(name,)).fetchone()
   db.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)));owned_databases.append(name)
def clear_databases():
 for dsn in (DSN,CP):
  with psycopg.connect(dsn,autocommit=True) as db:
   db.execute('DROP SCHEMA public CASCADE');db.execute('CREATE SCHEMA public')
def evidence(session_ids):
 with psycopg.connect(DSN) as db:
  rows=[dict(row) for row in db.cursor(row_factory=psycopg.rows.dict_row).execute('SELECT run_id,public_run_id,status,created_at,started_at,completed_at,updated_at,attempt_count FROM agent_runs WHERE session_id=ANY(%s) ORDER BY created_at',([UUID(v) for v in session_ids],))]
  for row in rows:
   row['queue_ms']=(row['started_at']-row['created_at']).total_seconds()*1000 if row['started_at'] else None
  command_rows=[dict(row) for row in db.cursor(row_factory=psycopg.rows.dict_row).execute(
    'SELECT c.command_id,c.event_id,c.execution_id,c.sequence,c.state,c.failure_attempts,c.created_at,c.updated_at FROM ew_commands c JOIN ew_bindings b USING(namespace,execution_id) WHERE b.session_id=ANY(%s) ORDER BY c.created_at',(session_ids,))]
  return {'commands':command_rows,'outbox_pending':db.execute("SELECT count(*) FROM ew_outbox WHERE state<>'SENT'").fetchone()[0],
    'inbox_pending':db.execute("SELECT count(*) FROM ew_inbox WHERE state='RECEIVED' AND execution_id IN (SELECT execution_id FROM ew_bindings)").fetchone()[0],
    'runs':rows,'session_owners':db.execute('SELECT count(*) FROM session_executions WHERE token IS NOT NULL').fetchone()[0],
    'recovery_tasks':db.execute('SELECT count(*) FROM tasks WHERE recovery_required').fetchone()[0],
    'rows':{name:db.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(name))).fetchone()[0] for name in ('messages','agent_run_logs','task_events')}}
async def trial(n,c,repeat):
 key=f'{a.scenario}-u{n}-c{c}-ew{a.event_concurrency}-d{a.delay_ms}-r{repeat}';folder=a.output/key;folder.mkdir();clear_databases()
 api_port=port();mock_port=port();namespace=stem+'-'+uuid4().hex[:6];origin=f'http://127.0.0.1:{api_port}'
 shared=Path(tempfile.mkdtemp(prefix='executor-fixture-',dir='/private/tmp'));mock_process=None;mock_log=None;mock_config=folder/'private-mock.json'
 executor_origin=private.get('EXECUTOR_BASE_URL','http://127.0.0.1:8000') if a.real_executor else f'http://127.0.0.1:{mock_port}'
 from urllib.parse import urlparse
 assert urlparse(executor_origin).hostname in ('localhost','127.0.0.1')
 event_stream='executor.events' if a.real_executor else namespace+':events'
 settings={'DATABASE_URL':crud.render_as_string(hide_password=False),'CHECKPOINT_DB_URI':CP,
  'EW_DATABASE_URL':DSN,'WORKFLOW_DATABASE_URL':DSN,'EW_NAMESPACE':namespace,'REDIS_URL':'redis://127.0.0.1:6379/0',
  'DATABASE_POOL_SIZE':a.pool,'DATABASE_MAX_OVERFLOW':0,'DATABASE_POOL_TIMEOUT_SECONDS':10,
  'CHECKPOINT_POOL_MIN_SIZE':1,'CHECKPOINT_POOL_MAX_SIZE':a.checkpoint_pool,'CHECKPOINT_SETUP_ON_START':True,
  'EW_POOL_SIZE':a.event_pool,'EW_CONCURRENCY':4,'EW_INGRESS_CONCURRENCY':4,'EW_DISPATCH_CONCURRENCY':a.event_concurrency,'EW_POLL_SECONDS':a.event_poll,'EW_IDLE_POLL_SECONDS':a.event_idle,
  'EW_HEALTH_PORT':0,'EW_EXECUTOR_EVENT_STREAM':event_stream,'MODEL_PROVIDER':'mock','MODEL_MOCK_DELAY_MS':a.delay_ms,'PHOENIX_ENDPOINT':None,
  'AGENT_WORKER_CONCURRENCY':c,'AGENT_WORKER_ENABLED':True,'EVENT_WORKER_ENABLED':True,'TASK_RECONCILER_ENABLED':False,
  'AGENT_WORKER_MAX_RETRIES':0,'AGENT_WORKER_POLL_INTERVAL_SECONDS':a.claim_seconds,'TASK_CANCEL_POLL_INTERVAL_SECONDS':a.cancel_seconds,
  'SSE_POLL_INTERVAL_SECONDS':a.sse_seconds,'EXECUTOR_SUBMIT_ENABLED':True,'EXECUTOR_BASE_URL':executor_origin,'EXECUTOR_SOURCE_TYPE':'INLINE',
  'EXECUTOR_SHARED_RESULT_ROOT':private['EXECUTOR_SHARED_RESULT_ROOT'] if a.real_executor else str(shared),
  'EXECUTOR_RUNTIME_PROFILE':'default','EXECUTOR_OPERATION_TIMEOUT_SECONDS':120,'EXECUTOR_OPERATION_WAIT_TIMEOUT_SECONDS':120,'WORKFLOW_PERSISTENCE_ENABLED':False,
  'RUN_DIAGNOSTICS_DIR':str(folder/'trace-disabled-by-probe'),'SHUTDOWN_DRAIN_SECONDS':5,'SHUTDOWN_TIMEOUT_SECONDS':15,
  'ANALYSIS_DATASETS':{'default-nce':{'title':'Fixed service test reference','runtime_path':'/workspace/pv/default_data/df_nce_long_format.parquet','scope':'GLOBAL'}},
  'SSO_PUBLIC_API_ORIGIN':origin,'SSO_FRONTEND_ORIGIN':origin,'SSO_COOKIE_SECURE':False,'SSO_NAMESPACE':namespace+':sso',
  'SSO_AUTO_REGISTER':True,'SSO_ALLOWED_ORIGINS':['https://sso.example.test']}
 if a.cache_size is not None:settings['DATABASE_PREPARED_STATEMENT_CACHE_SIZE']=a.cache_size
 config=folder/'private-config.json';private_json(config,{'settings':settings,'port':api_port,'namespace':namespace,'executor_probe':True,'hold_owner_probe':a.hold_owners})
 env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','LANG','LC_ALL')}
 env.update(PYTHONPATH=str(ROOT/'src'),PYTHONDONTWRITEBYTECODE='1')
 with tempfile.TemporaryDirectory(prefix='service-perf-config-') as directory:
  migration=Path(directory)/'config.yml';migration.write_text(yaml.safe_dump(settings));migration.chmod(0o600)
  with (folder/'migration.log').open('w') as f:
   for ini in ('alembic.crud.ini','alembic.ini'):
    subprocess.run([sys.executable,'-m','alembic','-c',ini,'upgrade','head'],cwd=ROOT,env={**env,'SERVICE_CONFIG_FILE':str(migration)},stdout=f,stderr=f,check=True)
 if not a.real_executor:
  private_json(mock_config,{'port':mock_port,'redis_url':settings['REDIS_URL'],'stream':event_stream,'result_root':str(shared),'delay_ms':a.executor_delay_ms})
  mock_log=(folder/'mock.log').open('w');(folder/'mock.log').chmod(0o600)
  mock_process=subprocess.Popen([sys.executable,str(Path(__file__).with_name('mock_executor.py')),'--config',str(mock_config)],env=env,cwd=ROOT,stdout=mock_log,stderr=mock_log)
 log=(folder/'server.log').open('w');(folder/'server.log').chmod(0o600);process=subprocess.Popen([sys.executable,str(Path(__file__).parents[1]/'service_throughput/server.py'),'--config',str(config)],env=env,cwd=ROOT,stdout=log,stderr=log)
 clients=[];samples=[];stop=asyncio.Event();sample_task=None;waiting=set();hold_proof=[];hold_task=None
 mock_ctl=httpx.AsyncClient(base_url=executor_origin,trust_env=False,timeout=30)
 try:
  async with httpx.AsyncClient(base_url=origin,trust_env=False,timeout=30) as ctl:
   if mock_process:
    for _ in range(200):
     if mock_process.poll() is not None:raise RuntimeError('Mock exited; inspect private mock.log')
     try:
      if (await mock_ctl.get('/_bench/ready')).status_code==200:break
     except httpx.HTTPError:pass
     await asyncio.sleep(.05)
    else:raise RuntimeError('Mock startup deadline')
   for _ in range(500):
    if process.poll() is not None:raise RuntimeError('Benchmark server exited; inspect local server.log')
    try:
     if (await ctl.get('/_bench/ready')).status_code==200:break
    except httpx.HTTPError:pass
    await asyncio.sleep(.05)
   else:raise RuntimeError('Startup deadline')
   async def login(i):
    client=httpx.AsyncClient(base_url=origin,trust_env=False,timeout=httpx.Timeout(30,read=max(120,n*6)),limits=httpx.Limits(max_connections=3))
    clients.append(client)
    r=await client.get('/api/v1/auth/login/sso',params={'employee':namespace+'-'+str(i)})
    assert r.status_code==302
    r=await client.get('/api/v1/users/me');r.raise_for_status();me=r.json()
    return client,{'X-CSRF-Token':me['csrf_token']}
   async def wait(client,path,rid,previous=None,terminal=False):
    began=time.perf_counter();sequences=[]
    async with client.stream('GET',path+'/'+rid+'/stream') as stream:
     stream.raise_for_status();event=None
     async for line in stream.aiter_lines():
      if line.startswith('event: '):event=line[7:]
      elif line.startswith('data: '):
       item=json.loads(line[6:])
       if 'sequence' in item:sequences.append(item['sequence'])
       if event=='run.snapshot':
        state=item['data'];assert state['status'] not in ('error','timeout','canceled','recovery_required'),state['status']
        if terminal and state['status']=='waiting_executor':waiting.add(rid)
        if (terminal and state['status']=='success') or (not terminal and state['status']=='waiting_input' and state['resume_token']!=previous):
         assert sequences==sorted(set(sequences))
         return state,(time.perf_counter()-began)*1000
    raise RuntimeError('SSE ended without expected Run state')
   async def flow(i,client,headers):
    began=time.perf_counter();latencies=[]
    async def req(method,path,body=None):
     start=time.perf_counter();r=await client.request(method,path,json=body,headers={**headers,'Idempotency-Key':str(uuid4())})
     latencies.append({'method':method,'ms':(time.perf_counter()-start)*1000,'status':r.status_code});r.raise_for_status()
     return r.json() if r.content else {}
    project=await req('POST','/api/v1/projects',{'project_name':'Throughput '+str(i)})
    session=await req('POST','/api/v1/projects/'+project['id']+'/sessions',{'session_name':'Fixed scenario','settings':{'kernel_profile':'default'}})
    sid=session['id'];path='/api/v1/sessions/'+sid+'/runs'
    accepted=await req('POST',path,{'input':{'content':[{'type':'text','text':'default-nce 데이터의 품질과 이상치를 분석해줘'}]}})
    rid=accepted['run_id'];state,initial_wait=await wait(client,path,rid);plan=state['interrupt'][0]['payload']['plans'][0]
    previous=state['resume_token'];revision=plan['plan_revision']
    await req('POST',path,{'run_id':rid,'resume_token':previous,'command':{'resume':{'action':'edit_plan','plan_id':plan['plan_id'],'plan_revision':revision,'execution_overrides':{'mode':'MULTI','repair_level':1,'max_repair_attempts':1}}}})
    state,edit_wait=await wait(client,path,rid,previous);plan=state['interrupt'][0]['payload']['plans'][0]
    assert plan['plan_revision']==revision+1
    await req('POST',path,{'run_id':rid,'resume_token':state['resume_token'],'command':{'resume':{'action':'approve_plan','plan_id':plan['plan_id'],'plan_revision':plan['plan_revision']}}})
    state,approval_wait=await wait(client,path,rid,terminal=True)
    final=state['result']['final_response']
    assert final['status']=='analysis_completed' and final['executor_status']=='SUCCEEDED'
    assert len(final['observations'])==4 and final['report']['status']=='ready'
    assert [o['step_id'] for o in final['observations']]==['load','profile','statistics','outliers']
    return {'user':i,'seconds':time.perf_counter()-began,'requests':latencies,'session_id':sid,'run_id':rid,'passed':True,'sse_wait_ms':[initial_wait,edit_wait,approval_wait],'execution_id':final['execution_id'],
      'report':final['report'],'observations':final['observations'],'terminal_at':time.perf_counter()}
   # Warm pools, graph and assets separately; exclude warm-up/user registration.
   warm,headers=await login(0);warm_result={'session_id':str(uuid4())} if a.real_executor else await flow(0,warm,headers)
   for _ in range(200):
    if (await asyncio.to_thread(evidence,[warm_result['session_id']]))['session_owners']==0:break
    await asyncio.sleep(.025)
   else:raise RuntimeError('Warmup owner did not release')
   users=[await login(i+1) for i in range(n)]
   await ctl.post('/_bench/reset')
   if mock_process:await mock_ctl.post('/_bench/reset')
   waiting.clear()
   if a.hold_seconds:
    await mock_ctl.post('/_bench/gate/hold')
    async def release_gate():
     async with asyncio.timeout(120):
      while len(waiting)!=n:await asyncio.sleep(.05)
     # The load controller runs outside the service. No hidden fixed wait in graph.
     for _ in range(max(3,int(a.hold_seconds/.25))):
      m=(await ctl.get('/_bench/metrics')).json()
      hold_proof.append({'at':time.perf_counter(),'waiting':len(waiting),'agent_active':m['current_worker'],
       'event_active':m['current_event_worker'],'crud_checkedout':m['crud_connections_checked_out'],
       'psycopg_pools':m['psycopg_pools'],'crud_owners':m.get('crud_owners',[])})
      await asyncio.sleep(.25)
     await mock_ctl.post('/_bench/gate/release')
    hold_task=asyncio.create_task(release_gate())
   async def sample():
    connection=await psycopg.AsyncConnection.connect(DSN,autocommit=True)
    try:
     while not stop.is_set():
      r=await connection.execute("SELECT count(*) FILTER (WHERE state='active'),count(*),count(*) FILTER (WHERE wait_event_type='Lock'),count(*) FILTER (WHERE state='idle in transaction') FROM pg_stat_activity WHERE datname=ANY(%s) AND pid<>pg_backend_pid()",([stem,stem+'_cp'],))
      active,total,lock,idle=await r.fetchone()
      ps=await asyncio.create_subprocess_exec('ps','-o','rss=','-p',str(process.pid),stdout=asyncio.subprocess.PIPE)
      stdout,_=await ps.communicate()
      samples.append({'at':time.perf_counter(),'rss_kib':int(stdout.strip() or 0),'db_connections':total,'db_active':active,'db_lock_waiters':lock,'db_idle_transactions':idle})
      try:await asyncio.wait_for(stop.wait(),.5)
      except TimeoutError:pass
    finally:await connection.close()
   sample_task=asyncio.create_task(sample());started=time.perf_counter()
   results=await asyncio.wait_for(asyncio.gather(*(flow(i+1,*value) for i,value in enumerate(users)),return_exceptions=True),max(180,n*8))
   elapsed=time.perf_counter()-started
   # Run state commit precedes owner release; collect after cleanup, not at first SSE.
   for _ in range(200):
    metrics=(await ctl.get('/_bench/metrics')).json()
    if metrics['current_worker']==0 and metrics['current_event_worker']==0 and metrics['crud_connections_checked_out']==0:break
    await asyncio.sleep(.025)
   if hold_task:await hold_task;hold_task=None
   mock_metrics=(await mock_ctl.get('/_bench/metrics')).json() if mock_process else {'real_executor':True}
   stop.set();await sample_task;sample_task=None
   completed=[r for r in results if isinstance(r,dict)];errors=[type(r).__name__ for r in results if isinstance(r,BaseException)]
   database=await asyncio.to_thread(evidence,[r['session_id'] for r in completed])
   private_json(folder/'before-validation.json',json.loads(json.dumps({'completed':completed,'errors':errors,'database':database,'server':metrics,'samples':samples,'mock':mock_metrics,'hold_proof':hold_proof},default=str)))
   assert metrics['crud_connections_checked_out']==0,'CRUD pool did not drain after SSE disconnects'
   expected=n*3
   assert len(database['runs'])==expected and database['session_owners']==0 and database['recovery_tasks']==0
   if a.scenario=='executor':
    assert all(r['attempt_count']==1 and r['queue_ms'] is not None for r in database['runs'])
    assert len(metrics['models'])==n and len(metrics['workers'])==expected
    assert metrics['peak_worker']<=c
   result={'config':{'users':n,'concurrency':c,'repeat':repeat,'scenario':a.scenario,'delay_ms':a.delay_ms,'service_pool':a.pool,'checkpoint_pool':a.checkpoint_pool,'bridge_pool':a.event_pool,'overflow':0,'sse_seconds':a.sse_seconds,'cancel_seconds':a.cancel_seconds,'claim_seconds':a.claim_seconds,'cache_size':a.cache_size,'baseline_commit':'cbe520f97089c8e120a561e544ce67e35079048a','event_concurrency':a.event_concurrency,'event_pool':a.event_pool,'event_ingress_concurrency':4,
      'event_poll':a.event_poll,'event_idle':a.event_idle,'executor_delay_ms':a.executor_delay_ms,'hold_seconds':a.hold_seconds,'real_executor':a.real_executor,'source_sha256':{str(path.relative_to(ROOT)):__import__('hashlib').sha256(path.read_bytes()).hexdigest() for path in sorted((ROOT/'src').rglob('*.py')) if '__pycache__' not in path.parts}},'elapsed_seconds':elapsed,'results':completed,'errors':errors,'server':metrics,'database':database,'samples':samples,'mock':mock_metrics,'hold_proof':hold_proof,'passed':len(completed)==n and not errors}
   private_json(folder/'raw.json',json.loads(json.dumps(result,default=str)))
   assert result['passed'],errors
   assert not mock_metrics.get('tasks_failed')
   assert all(x['state']=='DONE' and x['failure_attempts']==0 for x in database['commands'])
   assert database['outbox_pending']==0 and database['inbox_pending']==0
   assert metrics['peak_event_worker']<=a.event_concurrency
   assert len({h['event_id'] for h in metrics['event_handlers'] if not h['error']})==n*3
   assert len([r for r in metrics['roles'] if r['role']=='review'])==n
   assert len([r for r in metrics['roles'] if r['role']=='report'])==n
   print(json.dumps({'trial':key,'seconds':round(elapsed,3),'mean':round(sum(r['seconds'] for r in completed)/n,3),'passed':True}),flush=True)
 finally:
  stop.set()
  if sample_task:await asyncio.gather(sample_task,return_exceptions=True)
  if hold_task:hold_task.cancel();await asyncio.gather(hold_task,return_exceptions=True)
  await mock_ctl.aclose()
  await asyncio.gather(*(client.aclose() for client in clients),return_exceptions=True)
  if process.poll() is None:
   process.send_signal(signal.SIGTERM)
   try:await asyncio.to_thread(process.wait,20)
   except subprocess.TimeoutExpired:process.kill();await asyncio.to_thread(process.wait);raise RuntimeError('Forced benchmark API shutdown')
  if mock_process and mock_process.poll() is None:
   mock_process.send_signal(signal.SIGTERM);await asyncio.to_thread(mock_process.wait,15)
  if mock_log:mock_log.close()
  mock_config.unlink(missing_ok=True)
  import shutil
  shutil.rmtree(shared)
  config.unlink(missing_ok=True);log.close()
  import redis.asyncio as redis
  broker=redis.from_url('redis://127.0.0.1:6379/0')
  keys=[key async for key in broker.scan_iter(match=namespace+':*')]
  if a.real_executor:
   await broker.xgroup_destroy(event_stream,namespace+':ingress')
  if keys:await broker.delete(*keys)
  await broker.aclose()
async def main():
 try:
  setup_databases()
  for repeat in range(1,a.repeat+1):
   for n in a.users:
    for c in a.concurrency:await trial(n,c,repeat)
 finally:
  with psycopg.connect(admin,autocommit=True) as db:
   for name in reversed(owned_databases):
    db.execute('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s',(name,))
    db.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
  private_json(a.output/'cleanup.json',{'owned_scratch_databases_removed':True,'existing_databases_untouched':True})
asyncio.run(main())
