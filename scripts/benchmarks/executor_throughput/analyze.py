"""Validate full-path captures and calculate independently reproducible timing tables."""
import argparse,json,statistics
from collections import Counter
from pathlib import Path


def quantile(values,p):
    data=sorted(values)
    if not data:return None
    rank=(len(data)-1)*p;i=int(rank);f=rank-i
    return data[i]*(1-f)+data[min(i+1,len(data)-1)]*f


def analyze(raw, *, allow_transient_deferrals=False):
    cfg=raw['config'];n=cfg['users'];s=raw['server'];db=raw['database'];results=raw['results']
    assert raw['passed'] and not raw['errors'] and len(results)==n
    assert db['session_owners']==db['recovery_tasks']==db['outbox_pending']==db['inbox_pending']==0
    assert s['current_worker']==s['current_event_worker']==s['crud_connections_checked_out']==0
    assert len({m['run_id'] for m in s['models']})==n
    assert {m['run_id'] for m in s['models']}<= {r['run_id'] for r in db['runs']}
    assert len(s['models'])==n and len(s['workers'])==n*3 and len(db['runs'])==n*3
    assert len({r['run_id'] for r in db['runs']})==n*3
    assert len({r['public_run_id'] for r in db['runs']})==n
    assert all(r['attempt_count']==1 and r['queue_ms'] is not None for r in db['runs'])
    assert all(h['status']<400 for h in s['http'])
    assert all(c['state']=='DONE' and c['failure_attempts']==0 for c in db['commands'])
    attempts=s['event_handlers'];deferrals=[h for h in attempts if h['error']]
    assert allow_transient_deferrals or not deferrals
    assert all(h['error'] in {'DeferEvent','_HandoffPending'} for h in deferrals)
    handlers=[h for h in attempts if h['error'] is None];assert len(handlers)==n*3
    assert len({h['event_id'] for h in handlers})==n*3
    assert s['peak_worker']<=cfg['concurrency'] and s['peak_event_worker']<=cfg['event_concurrency']
    assert Counter(r['role'] for r in s['roles'])=={'review':n,'report':n}
    exec_ids={r['execution_id'] for r in results}
    commands=[c for c in db['commands'] if c['execution_id'] in exec_ids]
    assert len(commands)==n*3 and {c['command_id'] for c in commands}=={h['command_id'] for h in handlers}
    assert len(exec_ids)==n and {h['execution_id'] for h in handlers}==exec_ids
    assert all(r['passed'] and len(r['observations'])==4 and r['report']['status']=='ready' for r in results)
    mean=lambda xs:statistics.mean(xs) if xs else None
    event_wait=[];ingress=[];after_ingest=[];publication=[];dispatch=[];report_delivery=[]
    for result in results:
        end=next(h['end'] for h in handlers if h['execution_id']==result['execution_id'] and h['event_type']=='execution.completed')
        report_delivery.append((result['terminal_at']-end)*1000)
    if not cfg['real_executor']:
        mock=raw['mock'];assert not mock['tasks_failed'] and mock['pending']==0
        seen=[t for t in mock['executions'] if t['execution_id'] in exec_ids]
        assert len(seen)==n and all(t['operations']==2 and t['status']=='SUCCEEDED' for t in seen)
        calls=Counter(t['stage'] for t in mock['timeline'] if t['stage']!='event_published')
        assert calls=={'submit_accepted':n,'continue_accepted':n,'finalize_accepted':n}
        events={t['event_id']:t for t in mock['timeline'] if t['stage']=='event_published'}
        for h in handlers:
            published=events[h['event_id']]['at'];event_wait.append((h['start']-published)*1000)
            ingested=next(t for t in s['event_stages'] if t['method']=='ingest' and t['event_id']==h['event_id'])
            ingress.append((ingested['end']-published)*1000)
            after_ingest.append((h['start']-ingested['end'])*1000)
            sent=next(t for t in s['event_stages'] if t['method']=='finish_publications' and t['sent'] and h['command_id'] in t['command_ids'])
            publication.append((sent['start']-ingested['end'])*1000)
            dispatch.append((h['start']-sent['start'])*1000)
    # These sums are overlapping work, never additive E2E percentages.
    seconds=[r['seconds'] for r in results];queue=[r['queue_ms'] for r in db['runs']]
    per_user_queue=[]
    for public in {r['public_run_id'] for r in db['runs']}:
        per_user_queue.append(sum(r['queue_ms'] for r in db['runs'] if r['public_run_id']==public))
    query_count=sum(x['count'] for x in s['sql'])
    return {'users':n,'agent_concurrency':cfg['concurrency'],'event_concurrency':cfg['event_concurrency'],
      'delay_ms':cfg['delay_ms'],'executor_delay_ms':cfg['executor_delay_ms'],'real_executor':cfg['real_executor'],
      'mean_seconds':mean(seconds),'p95_seconds':quantile(seconds,.95),'makespan_seconds':raw['elapsed_seconds'],
      'completed_users_per_second':n/raw['elapsed_seconds'],'queue_total_per_user_ms':mean(per_user_queue),
      'queue_invocation_p95_ms':quantile(queue,.95),'planning_ms':mean([r['sse_wait_ms'][0] for r in results]),
      'edit_ms':mean([r['sse_wait_ms'][1] for r in results]),'approval_to_terminal_ms':mean([r['sse_wait_ms'][2] for r in results]),
      'model_ms':mean([(m['end']-m['start'])*1000 for m in s['models']]),
      'event_wait_mean_ms':mean(event_wait),'event_wait_p95_ms':quantile(event_wait,.95),
      'transport_ingest_mean_ms':mean(ingress),'ingest_to_handler_mean_ms':mean(after_ingest),
      'ingest_to_publish_mean_ms':mean(publication),'publish_to_handler_mean_ms':mean(dispatch),
      'event_handler_mean_ms':mean([(h['end']-h['start'])*1000 for h in handlers]),
      'event_handler_p95_ms':quantile([(h['end']-h['start'])*1000 for h in handlers],.95),
      'completion_to_sse_mean_ms':mean(report_delivery),'api_cpu_seconds_per_user':s['cpu_seconds']/n,
      'crud_sql_per_user':query_count/n,'sql_by_category':dict(Counter({k:sum(x['count'] for x in s['sql'] if x['category']==k) for k in {x['category'] for x in s['sql']}})),
      'pool_checkout_p95_ms':quantile(s['acquires'],.95),'loop_lag_p95_ms':quantile(s['loop_lag'],.95),
      'loop_lag_max_ms':max(s['loop_lag'],default=0),'peak_agent':s['peak_worker'],'peak_event':s['peak_event_worker'],
      'rss_peak_mib':max(x['rss_kib'] for x in raw['samples'])/1024,
      'db_connections_peak':max(x['db_connections'] for x in raw['samples']),
      'db_lock_waiters_peak':max(x['db_lock_waiters'] for x in raw['samples']),
      'db_idle_transaction_peak':max(x['db_idle_transactions'] for x in raw['samples']),
      'successes':n,'event_handler_attempts':len(attempts),'event_deferred_attempts':len(deferrals),
      'normal_without_deferrals':not deferrals,'validated':True}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('captures',type=Path,nargs='+');p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    rows=[]
    for folder in args.captures:
        assert json.loads((folder/'cleanup.json').read_text())['owned_scratch_databases_removed']
        for path in sorted(folder.glob('*/raw.json')):
            row=analyze(json.loads(path.read_text()));rows.append({'capture':str(path),**row})
    args.output.write_text(json.dumps(rows,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({'validated_trials':len(rows),'successful_users':sum(r['users'] for r in rows)}))
