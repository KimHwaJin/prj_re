"""Aggregate full-refactor measurements without treating censored users as completed."""
import argparse,collections,csv,gzip,hashlib,json,math,statistics
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--input',type=Path,nargs='+',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
def stat(xs):
 xs=sorted(xs)
 def q(frac):
  pos=(len(xs)-1)*frac;lo=math.floor(pos);hi=math.ceil(pos)
  return xs[lo]+(xs[hi]-xs[lo])*(pos-lo)
 return {'n':len(xs),'mean':statistics.fmean(xs),'p50':q(.5),'p95':q(.95),'min':xs[0],'max':xs[-1]} if xs else {'n':0,'mean':None,'p50':None,'p95':None,'min':None,'max':None}
rows=[];details=[];manifest=[];checks=[]
for base in a.input:
 for path in sorted(base.glob('*/raw.json')):
  r=json.loads(path.read_text());c=r['config'];s=r['server'];key=path.parent.name
  good=[x for x in r['scenarios'] if x['status']=='complete'];counts=collections.Counter(x['status'] for x in r['scenarios']);http=collections.Counter(str(x['status']) for x in r['requests']);resources=collections.Counter(x['operation'] for x in s['resources']);modes=collections.Counter(x['mode'] for x in s['models']);stages=r['stages'];runs={x['run_id']:x for x in r['database']['runs']}
  n=c['users'];elapsed=r['elapsed_s'];flow=c['scenario']=='flow'
  verify={'trial':key,'scenario_count_matches_users':len(r['scenarios'])==n,'unique_users':len({x['user'] for x in r['scenarios']})==n,'expected_stages_for_completed':all(len([x for x in stages if x['user']==u['user'] and x['status']=='interrupted'])==4 for u in good) if flow else True,'slot_limit':s['peak_graph']<=c['slots'],'model_delay_at_least_5s':all(x['ms']>=4990 for x in r['llm']['events'])}
  if flow and len(good)==n:
   verify['exact_four_calls_per_user']=len(r['llm']['events'])==4*n
   verify['exact_four_runs_per_user']=len(runs)==4*n
  checks.append(verify)
  qtimes=[];etimes=[];modeltimes=[];graph_times=[];residual=[]
  for u in good:
   ids={x['run_id'] for x in stages if x['user']==u['user']}
   if flow:
    qs=sum(runs[x]['queue_ms'] or 0 for x in ids)/1000;es=sum(runs[x]['execution_ms'] or 0 for x in ids)/1000
    ms=sum(x['ms'] for x in s['models'] if x['run_id'] in ids)/1000;gs=sum(x['ms'] for x in s['graphs'] if x['run_id'] in ids)/1000
    qtimes.append(qs);etimes.append(es);modeltimes.append(ms);graph_times.append(gs);residual.append(u['ms']/1000-qs-es)
  htypes=collections.defaultdict(list)
  for x in s['holds']:htypes[x['kind']].append(x['end']-x['start'])
  requeststats={kind:{'success_ms':stat([x['ms'] for x in r['requests'] if x['kind']==kind and x['status'] is not None and 200<=x['status']<300]),'requests':sum(x['kind']==kind for x in r['requests']),'http_errors':sum(x['kind']==kind and x['status'] is not None and x['status']>=400 for x in r['requests']),'no_response':sum(x['kind']==kind and x['status'] is None for x in r['requests'])} for kind in sorted({x['kind'] for x in r['requests']})}
  totals=stat([x['ms']/1000 for x in good]);queue=stat(qtimes);exe=stat(etimes);models=stat(modeltimes);graphs=stat(graph_times);remainder=stat(residual)
  rawname=key+'.json.gz';dest=a.output/'raw'/rawname;dest.parent.mkdir(exist_ok=True)
  with dest.open('wb') as f:
   with gzip.GzipFile(filename='',mode='wb',fileobj=f,mtime=0) as z:z.write(path.read_bytes())
  shutdown_path=path.parent/'shutdown.json';shutdown=json.loads(shutdown_path.read_text()) if shutdown_path.exists() else None
  manifest.append({'trial':key,'path':'raw/'+rawname,'raw_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'gzip_sha256':hashlib.sha256(dest.read_bytes()).hexdigest(),'bytes':dest.stat().st_size,'shutdown':shutdown})
  d={'trial':key,'config':c,'stop_reason':r['stop_reason'],'completed':counts['complete'],'errors':counts['error'],'censored':counts['censored'],'elapsed_s':elapsed,'completion_seconds':totals,'completed_user_queue_seconds':queue,'completed_user_execution_seconds':exe,'completed_user_model_seconds':models,'completed_user_graph_seconds':graphs,'completed_user_other_seconds':remainder,'resource_counts':dict(resources),'resource_ms':{op:stat([x['ms'] for x in s['resources'] if x['operation']==op and 'ms'in x]) for op in resources},'model_modes':dict(modes),'model_http_ms':stat([x['ms'] for x in r['llm']['events']]),'project_prompt_calls':sum(x['project_context_present'] for x in r['llm']['events']),'model_calls':len(r['llm']['events']),'llm_peak':r['llm']['peak'],'requests':requeststats,'http_statuses':dict(http),'db_hold_seconds_by_kind':{k:sum(v) for k,v in htypes.items()},'db_hold_seconds':sum(sum(v) for v in htypes.values()),'db_acquire_ms':stat([x['ms'] for x in s['acquires']]),'sql_ms':stat([x['ms'] for x in s['sql']]),'loop_lag_ms':stat([x['lag_ms'] for x in s['samples']]),'threads':stat([x['threads'] for x in s['samples']]),'psycopg_connections':stat([x['psycopg_connections'] for x in s['samples']]),'peak_graph':s['peak_graph'],'peak_worker':s['peak_worker'],'cpu_seconds':s['cpu_seconds'],'cpu_average_cores':s['cpu_seconds']/elapsed,'rss_peak_mib':s['rss_peak_bytes']/1024**2,'healthy':s['healthy'],'faults':s['faults'],'db_statuses':r['database']['run_statuses'],'recovery_tasks':r['database']['recovery_tasks'],'session_owners':r['database']['session_owners'],'stage_statistics':{name:{'n':len([x for x in stages if x['stage']==name]),'seconds':stat([x['ms']/1000 for x in stages if x['stage']==name]),'statuses':dict(collections.Counter(x['status'] for x in stages if x['stage']==name))} for name in sorted({x['stage'] for x in stages})},'shutdown':shutdown}
  details.append(d)
  rows.append({'trial':key,'scenario':c['scenario'],'users':n,'version':c['label'],'slots':c['slots'],'repeat':c['repeat'],'complete':counts['complete'],'errors':counts['error'],'censored':counts['censored'],'elapsed_s':elapsed,'complete_mean_s':totals['mean'],'complete_p95_s':totals['p95'],'mean_queue_s':queue['mean'],'mean_execution_s':exe['mean'],'mean_model_s':models['mean'],'mean_other_s':remainder['mean'],'db_hold_s':d['db_hold_seconds'],'worker_db_hold_s':sum(v for k,v in d['db_hold_seconds_by_kind'].items() if k=='worker'),'db_hold_s_per_run':d['db_hold_seconds']/len(runs) if runs else None,'graph_builds':resources['graph_build'],'pool_creates':resources['pool_construct'],'pool_closes':resources['pool_close'],'peak_graph':s['peak_graph'],'model_calls':len(r['llm']['events']),'model_sync':modes['sync'],'model_async':modes['async'],'project_prompt_calls':d['project_prompt_calls'],'background_crud_p95_ms':requeststats.get('background_crud',{}).get('success_ms',{}).get('p95'),'crud_p95_ms':requeststats.get('crud',{}).get('success_ms',{}).get('p95'),'run_get_p95_ms':requeststats.get('run_get',{}).get('success_ms',{}).get('p95'),'run_post_p95_ms':requeststats.get('run_post',{}).get('success_ms',{}).get('p95'),'http_errors':sum(x['http_errors'] for x in requeststats.values()),'http_no_response':sum(x['no_response'] for x in requeststats.values()),'http_requests':len(r['requests']),'loop_lag_p95_ms':d['loop_lag_ms']['p95'],'threads_peak':d['threads']['max'],'cpu_s':s['cpu_seconds'],'cpu_average_cores':d['cpu_average_cores'],'rss_peak_mib':d['rss_peak_mib'],'stop_reason':r['stop_reason']})
(a.output/'summary.json').write_text(json.dumps(details,indent=2,ensure_ascii=False))
(a.output/'manifest.json').write_text(json.dumps(manifest,indent=2))
(a.output/'validation.json').write_text(json.dumps({'passed':all(all(v for k,v in x.items() if k!='trial') for x in checks),'checks':checks},indent=2))
if rows:
 with (a.output/'trials.csv').open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
for r in rows:print(json.dumps(r,ensure_ascii=False))
