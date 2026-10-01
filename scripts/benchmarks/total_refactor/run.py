"""Full-refactor comparison. Immutable c751822 -> 4bb5c2f, LLM HTTP delay=5s."""
import argparse,asyncio,json,os,signal,socket,subprocess,sys,time
from contextlib import AsyncExitStack
from pathlib import Path
from uuid import uuid4
import httpx,psycopg
from sqlalchemy.engine import make_url
ROOT=Path(__file__).resolve().parents[3];PY=sys.executable
REFS={'before':'c75182266b14103bed509ddd1ccb5004cd21eb2e','after':'4bb5c2fc3dcb4475525ff288eab8b0b887b37525'}
p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--matrix',choices=['smoke','crud','flow','control'],default='smoke');p.add_argument('--users',type=int,nargs='*');p.add_argument('--labels',nargs='*',choices=['before','after']);p.add_argument('--repeat',type=int,default=1);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
u=make_url(os.environ['DTEST_BENCH_DATABASE_URL'])
if u.host not in ('localhost','127.0.0.1') or u.database!='identity_test':raise SystemExit('Dedicated local identity_test required; public schema is destroyed per trial')
DSN=u.set(drivername='postgresql').render_as_string(hide_password=False)
source_root=a.output/'sources';source_root.mkdir(exist_ok=True)
for label,ref in REFS.items():
 dest=source_root/label
 if not dest.exists():
  dest.mkdir();archive=a.output/f'{label}.tar';archive.write_bytes(subprocess.check_output(['git','archive',ref],cwd=ROOT));subprocess.run(['tar','-xf',str(archive.resolve()),'-C',str(dest.resolve())],check=True);archive.unlink()
def port():
 with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
def db_evidence():
 with psycopg.connect(DSN) as db:
  runs=[dict(zip(['run_id','session_id','status','created_at','started_at','completed_at','updated_at','attempt_count','failure'],r)) for r in db.execute('SELECT run_id,session_id,status,created_at,started_at,completed_at,updated_at,attempt_count,failure FROM agent_runs ORDER BY created_at')]
  for r in runs:
   r['queue_ms']=(r['started_at']-r['created_at']).total_seconds()*1000 if r['started_at'] else None
   finish=r['completed_at'] or (r['updated_at'] if r['status']=='interrupted' else None)
   r['execution_ms']=(finish-r['started_at']).total_seconds()*1000 if finish and r['started_at'] else None
  recovery='recovery_required' in [r[0] for r in db.execute("SELECT column_name FROM information_schema.columns WHERE table_name='tasks'")]
  owners=db.execute("SELECT to_regclass('public.session_executions')").fetchone()[0] is not None
  return {'runs':runs,'run_statuses':dict(db.execute('SELECT status,count(*) FROM agent_runs GROUP BY status').fetchall()),'recovery_tasks':db.execute('SELECT count(*) FROM tasks WHERE recovery_required').fetchone()[0] if recovery else None,'session_owners':db.execute('SELECT count(*) FROM session_executions WHERE token IS NOT NULL').fetchone()[0] if owners else None,'messages':db.execute('SELECT count(*) FROM messages').fetchone()[0],'logs':db.execute('SELECT count(*) FROM agent_run_logs').fetchone()[0]}
async def trial(n,label,scenario,slots):
 key=f'{scenario}-{n}-{label}-s{slots}-r{a.repeat}';folder=a.output/key;folder.mkdir(exist_ok=True)
 with psycopg.connect(DSN,autocommit=True) as db:db.execute('DROP SCHEMA public CASCADE');db.execute('CREATE SCHEMA public')
 model_port,api_port=port(),port();source=(source_root/label).resolve()
 settings={'DATABASE_URL':os.environ['DTEST_BENCH_DATABASE_URL'],'CHECKPOINT_DB_URI':DSN,'AGENT_CHECKPOINT_DATABASE_URL':DSN,'EW_DATABASE_URL':DSN,'WORKFLOW_DATABASE_URL':DSN,'EW_REDIS_URL':'redis://127.0.0.1:1/0','REDIS_URL':'redis://127.0.0.1:1/0','EW_NAMESPACE':'benchmark-local','EW_POOL_SIZE':4,'DATABASE_POOL_SIZE':10,'DATABASE_MAX_OVERFLOW':0,'DATABASE_POOL_TIMEOUT_SECONDS':5,'CHECKPOINT_POOL_MIN_SIZE':1,'CHECKPOINT_POOL_MAX_SIZE':4,'CHECKPOINT_POOL_TIMEOUT_SECONDS':10,'CHECKPOINT_SETUP_ON_START':True,'MODEL_PROVIDER':'openai_compatible','MODEL_NAME':'benchmark-5s','MODEL_API_KEY':'local-only','API_BASE_URL':f'http://127.0.0.1:{model_port}/v1','MODEL_TIMEOUT_SECONDS':30,'MODEL_MAX_RETRIES':0,'MODEL_STRUCTURED_OUTPUT_MODE':'prompt_json','DATA_MOCK':True,'DEMO_ARTIFACTS_ENABLED':False,'EXECUTOR_SUBMIT_ENABLED':False,'GRAPH_CHECKPOINTER':'postgres','TASK_RECONCILER_ENABLED':False,'EVENT_WORKER_ENABLED':False,'AGENT_WORKER_ENABLED':True,'AGENT_WORKER_CONCURRENCY':slots,'AGENT_WORKER_MAX_RETRIES':0,'AGENT_WORKER_POLL_INTERVAL_SECONDS':.05,'TASK_CANCEL_POLL_INTERVAL_SECONDS':.25,'RUN_MONITOR_TIMEOUT_SECONDS':6,'RUN_CLEANUP_TIMEOUT_SECONDS':3,'TASK_LEASE_SECONDS':300,'SHUTDOWN_DRAIN_SECONDS':1,'SHUTDOWN_TIMEOUT_SECONDS':4,'WORKFLOW_STORAGE_ROOT':str(folder.resolve()/'workflows'),'EXECUTOR_SHARED_INPUT_ROOT':str(folder.resolve()/'executor-files'),'PHOENIX_CONFIG_PATH':str(folder.resolve()/'empty.yml')}
 (folder/'empty.yml').write_text('{}')
 if label=='after':
  settings.pop('AGENT_CHECKPOINT_DATABASE_URL',None);settings.pop('PHOENIX_CONFIG_PATH',None)
 # Child processes inherit only OS/runtime basics plus explicitly local settings.
 env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','LANG','LC_ALL','SYSTEMROOT')}
 env.update({k:str(v).lower() if isinstance(v,bool) else str(v) for k,v in settings.items()});env['PYTHONPATH']=str(source/'src');env['PYTHONDONTWRITEBYTECODE']='1'
 # New config is supplied explicitly; legacy Settings uses these same env values.
 configuration=folder/'settings.json';configuration.write_text(json.dumps(settings))
 env['SERVICE_CONFIG_FILE']=str(configuration.resolve())
 with (folder/'migration.log').open('w') as f:
  for ini in ('alembic.crud.ini','alembic.ini'):
   subprocess.run([PY,'-m','alembic','-c',ini,'upgrade','head'],cwd=source,env=env,stdout=f,stderr=f,check=True)
 # Provision checkpoint DDL before workloads. Concurrent-index migration while
 # an old Run already owns a transaction is not a steady-state service test.
 from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
 async with AsyncPostgresSaver.from_conn_string(DSN) as saver:await saver.setup()
 cfg={'label':label,'commit':REFS[label],'users':n,'slots':slots,'pool':10,'port':api_port,'scenario':scenario,'repeat':a.repeat,'delay_seconds':5,'poll_seconds':1,'think_seconds':.2,'settings':settings}
 configuration.write_text(json.dumps(cfg))
 # cfg contains only scratch credentials and is removed after process exit.
 model_log=(folder/'model.log').open('w');server_log=(folder/'server.log').open('w')
 model=subprocess.Popen([PY,str(ROOT/'scripts/benchmarks/total_refactor/mock_llm.py'),'--port',str(model_port)],env=env,stdout=model_log,stderr=model_log)
 child=subprocess.Popen([PY,str(ROOT/'scripts/benchmarks/total_refactor/server.py'),'--source',str(source),'--config',str(configuration.resolve())],cwd=source,env=env,stdout=server_log,stderr=server_log)
 requests=[];scenarios=[];stages=[];progress=[];background_tasks=[];start=None;stop_reason=None
 try:
  async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{api_port}',timeout=15) as ctl,httpx.AsyncClient(base_url=f'http://127.0.0.1:{model_port}',timeout=15) as llm,AsyncExitStack() as stack:
   for _ in range(300):
    if child.poll() is not None:raise RuntimeError(f'{key}: server exited')
    try:
     if (await ctl.get('/_bench/ready')).json()['ready'] and (await llm.get('/health')).json()['ready']:break
    except (httpx.HTTPError,ValueError,KeyError):pass
    await asyncio.sleep(.1)
   else:raise RuntimeError('startup deadline')
   users=[];clients={}
   for i in range(n):
    payload={'user_name':f'User {i}'} if label=='before' else {'user_id':f'user-{i}','user_name':f'User {i}','role':'user'}
    response=await ctl.post('/api/v1/users',headers={} if label=='before' else {'X-User-Id':'admin'},json=payload);response.raise_for_status();user=response.json()
    user['headers']={'Authorization':'Bearer '+user['user_id']} if label=='before' else {'X-User-Id':user['user_id']}
    if label=='before':
     resp=await ctl.get('/api/v1/projects',headers=user['headers']);resp.raise_for_status();user['default_project_id']=next(x['id'] for x in resp.json()['items'] if x['is_default'])
    (await ctl.patch(f"/api/v1/projects/{user['default_project_id']}",headers=user['headers'],json={'system_prompt':'BENCH_PROJECT_CONTEXT: use project-specific instructions.'})).raise_for_status()
    users.append(user);clients[i]=await stack.enter_async_context(httpx.AsyncClient(base_url=f'http://127.0.0.1:{api_port}',timeout=15,limits=httpx.Limits(max_connections=2,max_keepalive_connections=2)))
    (await clients[i].get(f"/api/v1/projects/{user['default_project_id']}",headers=user['headers'])).raise_for_status()
   bgclient=await stack.enter_async_context(httpx.AsyncClient(base_url=f'http://127.0.0.1:{api_port}',timeout=15,limits=httpx.Limits(max_connections=100,max_keepalive_connections=100)))
   await ctl.post('/_bench/reset');await llm.post('/reset');start=time.perf_counter();stop=asyncio.Event()
   async def request(method,path,index,kind,**kw):
    user=users[index];headers=dict(user['headers'])
    if kind=='run_post':headers['Idempotency-Key']=str(uuid4())
    t=time.perf_counter();status=None;error=None;server_ms=None
    try:
     response=await (bgclient if kind=='background_crud' else clients[index]).request(method,path,headers=headers,**kw)
     status=response.status_code;server_ms=float(response.headers['X-Bench-Server-Ms']) if 'X-Bench-Server-Ms' in response.headers else None
     response.raise_for_status();return response.json() if response.content else {}
    except Exception as exc:error=type(exc).__name__;raise
    finally:requests.append({'kind':kind,'user':index,'start_s':t-start,'ms':(time.perf_counter()-t)*1000,'status':status,'error':error,'server_ms':server_ms})
   async def job(i):
    began=time.perf_counter();outcome='complete';error=None
    try:
     if scenario=='crud':
      for rep in range(3):
       pr=await request('POST','/api/v1/projects',i,'crud',json={'project_name':f'project-{rep}'})
       se=await request('POST',f"/api/v1/projects/{pr['id']}/sessions",i,'crud',json={'session_name':'test'})
       await request('GET',f"/api/v1/sessions/{se['id']}",i,'crud')
       await request('PATCH',f"/api/v1/sessions/{se['id']}",i,'crud',json={'session_name':'updated'})
       await request('DELETE',f"/api/v1/sessions/{se['id']}",i,'crud');await request('DELETE',f"/api/v1/projects/{pr['id']}",i,'crud');await asyncio.sleep(.1)
     else:
      se=await request('POST',f"/api/v1/projects/{users[i]['default_project_id']}/sessions",i,'session_create',json={'session_name':'analysis'})
      previous=None
      for step,command in enumerate([None,'mock',{'objective':'EDA service comparison'},{'candidate_number':1}]):
       body={'input':{'messages':[{'role':'user','content':'불량 예측 서비스 비교'}]}} if command is None else {'command':command,'metadata':{'resume_run_id':previous}}
       t=time.perf_counter();r=await request('POST',f"/api/v1/sessions/{se['id']}/runs",i,'run_post',json=body);rid=r.get('run_id', r.get('id'));polls=0
       while r['status'] in ('pending','running'):
        await asyncio.sleep(1);polls+=1;r=await request('GET',f"/api/v1/sessions/{se['id']}/runs/{rid}",i,'run_get')
       actions=[x.get('name') for it in r.get('interrupt') or [] for x in it.get('action_requests',[])]
       expected=['data_selection','analysis_context','workflow_candidate_selection','workflow_approval'][step]
       stages.append({'user':i,'run_id':rid,'stage':expected,'ms':(time.perf_counter()-t)*1000,'polls':polls,'status':r['status'],'actions':actions,'failure':r.get('failure')})
       if r['status']!='interrupted' or actions!=[expected]:raise RuntimeError('unexpected run result')
       previous=rid
       if step<3:await asyncio.sleep(.2)
    except asyncio.CancelledError:outcome='censored';error=stop_reason or 'measurement stopped'
    except Exception as exc:outcome='error';error=str(exc)[:300]
    finally:scenarios.append({'user':i,'ms':(time.perf_counter()-began)*1000,'status':outcome,'error':error,'finished_s':time.perf_counter()-start})
   async def background():
    counter=0
    async def op(j):
     i=j%n
     try:
      if j%2:await request('GET',f"/api/v1/projects/{users[i]['default_project_id']}",i,'background_crud')
      else:await request('POST',f"/api/v1/projects/{users[i]['default_project_id']}/sessions",i,'background_crud',json={'session_name':'background'})
     except Exception:pass
    while not stop.is_set():
     background_tasks.append(asyncio.create_task(op(counter)));counter+=1
     await asyncio.sleep(max(0,start+counter/20-time.perf_counter()))
   bg=asyncio.create_task(background()) if scenario!='crud' else None
   jobs=[asyncio.create_task(job(i)) for i in range(n)]
   deadline=max(90,n*25+60);last_signature=None;last_progress=start
   while any(not j.done() for j in jobs):
    await asyncio.wait(jobs,timeout=2,return_when=asyncio.ALL_COMPLETED)
    now=time.perf_counter()
    if scenario!='crud':
     state=(await ctl.get('/_bench/progress')).json();signature=(state['models'],state['graphs'],state['workers'],len(stages))
     progress.append({'seconds':now-start,**state,'completed_users':len(scenarios)})
     if signature!=last_signature:last_signature=signature;last_progress=now
     if now-last_progress>45:stop_reason='no model/graph/Run/stage progress for 45 seconds'
    if now-start>deadline:stop_reason=f'measurement deadline {deadline}s'
    if stop_reason:
     for j in jobs:j.cancel()
     break
   await asyncio.gather(*jobs);workload_end=time.perf_counter();stop.set()
   if bg:await bg
   await asyncio.gather(*background_tasks)
   metrics=(await ctl.get('/_bench/metrics')).json();model_metrics=(await llm.get('/metrics')).json();evidence=await asyncio.to_thread(db_evidence)
   public={k:v for k,v in cfg.items() if k not in ('settings','port')}
   result={'config':public,'elapsed_s':max((s['finished_s'] for s in scenarios),default=workload_end-start),'stop_reason':stop_reason,'requests':requests,'scenarios':scenarios,'stages':stages,'server':metrics,'llm':model_metrics,'database':evidence,'progress':progress}
   (folder/'raw.json').write_text(json.dumps(result,default=str,separators=(',',':')))
   print(json.dumps({'trial':key,'seconds':round(result['elapsed_s'],2),'complete':sum(s['status']=='complete' for s in scenarios),'users':n,'censored':sum(s['status']=='censored' for s in scenarios),'http_errors':sum(bool(r['error']) for r in requests),'models':len(model_metrics['events']),'stop_reason':stop_reason}),flush=True)
 finally:
  shutdown={}
  for name,proc in [('api',child),('model',model)]:
   began=time.perf_counter();forced=False
   if proc.poll() is None:
    proc.send_signal(signal.SIGTERM)
    try:await asyncio.to_thread(proc.wait,12)
    except subprocess.TimeoutExpired:forced=True;proc.kill();await asyncio.to_thread(proc.wait)
   shutdown[name]={'seconds':time.perf_counter()-began,'forced_kill':forced,'returncode':proc.returncode}
  (folder/'shutdown.json').write_text(json.dumps(shutdown));configuration.unlink(missing_ok=True);model_log.close();server_log.close()

async def main():
 ns=a.users or ([1] if a.matrix=='smoke' else [1,10] if a.matrix=='control' else [1,10,30,50,100])
 for n in ns:
  labels=a.labels or (['before','after'] if a.repeat%2 else ['after','before'])
  for label in labels:
   await trial(n,label,'crud' if a.matrix=='crud' else 'flow',1 if label=='before' or a.matrix=='control' else 4)
asyncio.run(main())
