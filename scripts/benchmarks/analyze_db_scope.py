"""Aggregate saved A/B measurements; keep failures and trial variation visible."""
import argparse, csv, gzip, json, math, statistics
from pathlib import Path
from collections import defaultdict
p=argparse.ArgumentParser()
p.add_argument('inputs',nargs='+',type=Path)
p.add_argument('--output',required=True,type=Path)
a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)

def pct(values,p):
    if not values:return None
    s=sorted(values);return s[max(0,math.ceil(len(s)*p)-1)]
def avg(x):return statistics.mean(x) if x else None

def summarize(data):
    conf=data[0]['config'];req=[x for d in data for x in d['requests']]
    scenarios=[x for d in data for x in d['scenarios']]
    runs=[x for d in data for x in d['database']['runs']]
    holds=[x for d in data for x in d['server']['holds']]
    gets=[x for d in data for x in d['server']['checkouts']]
    sqls=[x for d in data for x in d['server']['sql']]
    models=[x for d in data for x in d['server']['models']]
    elapsed=sum(d['elapsed_s'] for d in data)
    row={k:conf[k] for k in ('name','scenario','users','delay_ms','pool','slots','label')}
    row.update(repeats=len(data),complete=sum(x['status']=='complete' for x in scenarios),attempted=len(scenarios),
        http_requests=len(req),http_errors=sum(x.get('error') is not None for x in req),
        mean_trial_seconds=elapsed/len(data),trial_min_s=min(d['elapsed_s'] for d in data),trial_max_s=max(d['elapsed_s'] for d in data),
        completed_scenarios_per_second=sum(x['status']=='complete' for x in scenarios)/elapsed,
        scenario_mean_ms=avg([x['ms'] for x in scenarios if x['status']=='complete']),
        scenario_p95_ms=pct([x['ms'] for x in scenarios if x['status']=='complete'],.95),
        queue_mean_ms=avg([x['queue_ms'] for x in runs if x['queue_ms'] is not None]),
        queue_p95_ms=pct([x['queue_ms'] for x in runs if x['queue_ms'] is not None],.95),
        execution_mean_ms=avg([x['execution_ms'] for x in runs if x['execution_ms'] is not None]),
        run_records=len(runs),unstarted=sum(x['started_at'] is None for x in runs),
        healthy_trials=sum(d['server']['healthy'] for d in data),
        recovery_tasks=sum(d['database']['recovery_tasks'] for d in data),
        db_hold_seconds_per_trial=sum(x['ms'] for x in holds)/1000/len(data),
        db_hold_seconds_per_run=sum(x['ms'] for x in holds)/1000/len(runs) if runs else None,
        worker_hold_seconds_per_run=sum(x['ms'] for x in holds if x['kind']=='worker')/1000/len(runs) if runs else None,
        hold_max_ms=max((x['ms'] for x in holds),default=0),
        worker_hold_p99_ms=pct([x['ms'] for x in holds if x['kind']=='worker'],.99),
        checkout_mean_ms=avg([x['ms'] for x in gets]),checkout_p95_ms=pct([x['ms'] for x in gets],.95),
        checkout_p99_ms=pct([x['ms'] for x in gets],.99),checkout_max_ms=max((x['ms'] for x in gets),default=0),
        mean_checked_out=avg([x['checked_out'] for d in data for x in d['server']['samples']]),
        peak_checked_out=max((x['checked_out'] for d in data for x in d['server']['samples']),default=0),
        sql_count_per_run=len(sqls)/len(runs) if runs else None,
        worker_selects_per_trial=sum(x['kind']=='worker' and x['verb']=='SELECT' for x in sqls)/len(data),
        sql_mean_ms=avg([x['ms'] for x in sqls]),sql_p95_ms=pct([x['ms'] for x in sqls],.95),
        model_count=len(models),model_mean_ms=avg(models),
        cpu_core_fraction=sum(d['server'].get('cpu_seconds',0) for d in data)/elapsed,
        loop_lag_p95_ms=pct([x['lag_ms'] for d in data for x in d['server']['samples']],.95),
        peak_graph=max(d['server']['peak_graph'] for d in data))
    for kind in ('crud','background_crud','run_post','run_get','session_create'):
        rr=[x for x in req if x['kind']==kind];success=[x['ms'] for x in rr if not x.get('error')]
        for name,value in [('requests',len(rr)),('errors',sum(bool(x.get('error')) for x in rr)),('mean_ms',avg(success)),('p95_ms',pct(success,.95)),('p99_ms',pct(success,.99)),('server_p95_ms',pct([x['server_ms'] for x in rr if not x.get('error') and x.get('server_ms') is not None],.95)),('server_mean_ms',avg([x['server_ms'] for x in rr if not x.get('error') and x.get('server_ms') is not None])),('outside_server_mean_ms',avg([max(0,x['ms']-x['server_ms']) for x in rr if not x.get('error') and x.get('server_ms') is not None]))]:row[kind+'_'+name]=value
    row['sql_by_kind']={kind:sum(x['kind']==kind for x in sqls) for kind in sorted({x['kind'] for x in sqls})}
    row['request_kind_counts']={kind:sum(x['kind']==kind for x in req) for kind in sorted({x['kind'] for x in req})}
    row['stage_mean_ms']={name:avg([x['ms'] for d in data for x in d['stages'] if x['stage']==name and x['status']=='interrupted']) for name in ('data_selection','analysis_context','workflow_candidate_selection','workflow_approval')}
    row['http_by_status']=dict(__import__('collections').Counter(str(x['status']) for x in req))
    return row

all_data=[];paths=[]
for folder in a.inputs:
    for f in sorted(folder.glob('*/raw.json')):
        if 'pilot-' in f.parent.name:continue
        d=json.loads(f.read_text());all_data.append(d);paths.append(f)
        # Integrity checks before any headline calculations.
        assert len(d['scenarios'])==d['config']['users'],f
        assert len({x['user'] for x in d['scenarios']})==d['config']['users'],f
        assert all(x['ms']>=0 for x in d['requests']),f
        assert d['server']['peak_graph']<=d['config']['slots'],f
        if all(x['status']=='complete' for x in d['scenarios']) and d['config']['scenario']!='crud':
            expected=d['config']['users']*(4 if d['config']['scenario']=='flow' else 1)
            assert len(d['database']['runs'])==expected,(f,'run count')
            assert len(d['stages'])==expected,(f,'stage count')
            assert all(x['status']=='interrupted' for x in d['database']['runs']),(f,'run statuses')
            assert d['database']['session_owners']==0,(f,'leaked ownership')
            assert d['database']['recovery_tasks']==0,(f,'recovery')
groups=defaultdict(list)
for d in all_data:groups[(d['config']['name'],d['config']['label'])].append(d)
rows=[summarize(v) for k,v in sorted(groups.items())]
(a.output/'summary.json').write_text(json.dumps(rows,indent=2,ensure_ascii=False))
keys=[k for k in rows[0] if k not in ('stage_mean_ms','http_by_status','sql_by_kind','request_kind_counts')] if rows else []
with (a.output/'summary.csv').open('w') as f:
    w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows({k:r[k] for k in keys} for r in rows)
stage_groups=defaultdict(list)
for d in all_data:
    run_map={r['run_id']:r for r in d['database']['runs']}
    model_map=defaultdict(float)
    for m in d['server'].get('model_events',[]):model_map[m['run_id']]+=m['ms']
    graph_map={r['run_id']:r['ms'] for r in d['server']['graphs'] if r.get('run_id')}
    for stage in d['stages']:
        r=run_map.get(stage['run_id'])
        if not r or r['execution_ms'] is None or stage['status']!='interrupted':continue
        stage_groups[(d['config']['name'],d['config']['label'],stage['stage'])].append({
            'queue_ms':r['queue_ms'],'execution_ms':r['execution_ms'],'model_ms':model_map[stage['run_id']],
            'other_execution_ms':r['execution_ms']-model_map[stage['run_id']],
            'graph_ms':graph_map.get(stage['run_id']),'client_ms':stage['ms'],
            'observation_and_http_ms':stage['ms']-r['queue_ms']-r['execution_ms']})
stage_rows=[]
for (name,label,stage),values in sorted(stage_groups.items()):
    row={'name':name,'label':label,'stage':stage,'n':len(values)}
    row.update({k:avg([x[k] for x in values if x[k] is not None]) for k in values[0]})
    stage_rows.append(row)
(a.output/'stage-breakdown.json').write_text(json.dumps(stage_rows,indent=2))
trials=[summarize([d]) for d in all_data]
(a.output/'trials.json').write_text(json.dumps(trials,indent=2))
comparisons=[]
for name in sorted({x['name'] for x in rows}):
    pair={x['label']:x for x in rows if x['name']==name}
    if set(pair)!= {'before','after'}:continue
    b,c=pair['before'],pair['after'];entry={'name':name,'users':b['users'],'scenario':b['scenario'],'before':b,'after':c,'reduction_percent':{}}
    for metric in ('scenario_mean_ms','background_crud_p95_ms','run_get_p95_ms','crud_p95_ms','db_hold_seconds_per_trial','worker_hold_seconds_per_run','queue_mean_ms','execution_mean_ms'):
        bv,cv=b.get(metric),c.get(metric)
        entry['reduction_percent'][metric]=(bv-cv)/bv*100 if bv and cv is not None else None
    comparisons.append(entry)
(a.output/'comparisons.json').write_text(json.dumps(comparisons,indent=2))
with gzip.open(a.output/'raw-trials.jsonl.gz','wt') as f:
    for d in all_data:f.write(json.dumps(d,separators=(',',':'))+'\n')
(a.output/'validation.json').write_text(json.dumps({'trials':len(all_data),'paired_cases':len(comparisons),'scenario_attempts':sum(len(d['scenarios']) for d in all_data),
    'http_requests':sum(len(d['requests']) for d in all_data),'checked':'unique scenario count, nonnegative durations, graph concurrency cap, completed stage/run counts, terminal ownership and recovery flags',
    'aggregation':'Nearest-rank percentiles pooled across repeat requests; duration/rate sums across repeats; min/max trial durations retained. Failure latency is excluded from success percentiles but error counts and censored scenarios are explicit.'},indent=2))
print(json.dumps({'trials':len(all_data),'paired_cases':len(comparisons)}))
for x in comparisons:
 b,c=x['before'],x['after']
 print(x['name'],f"complete {b['complete']}/{b['attempted']} -> {c['complete']}/{c['attempted']}", 'seconds',round(b['mean_trial_seconds'],2),round(c['mean_trial_seconds'],2),'hold%',round(x['reduction_percent']['db_hold_seconds_per_trial'],1))
