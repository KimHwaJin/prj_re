"""Validate complete cohorts before deriving bounded burst performance metrics."""
import argparse
from collections import Counter
import gzip
import json
from pathlib import Path
import statistics


def quantile(values,p=.95):
    data=sorted(values)
    if not data:return None
    rank=(len(data)-1)*p;i=int(rank)
    return data[i]+(data[min(i+1,len(data)-1)]-data[i])*(rank-i)


def analyze(raw):
    from observation_scenarios import scenario as observation_scenario
    spec=observation_scenario(raw['config'].get('observation_profile','standard'))
    cfg=raw['config'];n=cfg['users'];s=raw['server'];db=raw['database'];results=raw['results']
    scenario=cfg['scenario'];burst=scenario in ('result_burst','mixed');approval=scenario=='approval';followup=cfg['followup']
    assert raw['passed'] and not raw['errors'] and len(results)==n
    assert len({r['session_id'] for r in results})==n
    assert all(r['passed'] for r in results)
    assert db['session_owners']==db['recovery_tasks']==db['outbox_pending']==db['inbox_pending']==0
    assert s['current_shared']==s['current_worker']==s['current_event_worker']==s['crud_connections_checked_out']==0
    assert s['peak_shared']<=cfg['total_capacity']
    if burst:
        proof=raw['hold_proof']
        assert len(proof)>=3
        assert all(v['waiting']==(n if scenario=='result_burst' else (n+1)//2) for v in proof)
        assert all(v['agent_active']==v['event_active']==0 for v in proof)
        # Background SSE/housekeeping can briefly acquire a connection in a sample.
        # A zero sample proves it was not continuously held across this observation.
        assert any(v['crud_checkedout']==0 for v in proof)
    if cfg['architecture']=='split':
        assert s['peak_worker']<=cfg['user_capacity'] and s['peak_event_worker']<=cfg['event_concurrency']
    assert all(h['status']<400 for h in s['http'])
    expected=n*(2 if approval else 5 if followup else 3)
    assert len(db['runs'])==len({r['run_id'] for r in db['runs']})==expected
    assert all(r['attempt_count']==1 and r['queue_ms'] is not None for r in db['runs'])
    assert Counter(r['status'] for r in db['runs'])==({'interrupted':n*2} if approval else {'interrupted':n*2,'success':n*(3 if followup else 1)})
    assert len({r['public_run_id'] for r in db['runs']})==n*(3 if followup else 1)
    commands=db['common_commands']
    if cfg['architecture']=='common':
        assert len(commands)==n*(2 if approval else 8 if followup else spec.operations+4)
        assert len({c['command_id'] for c in commands})==len(commands)
        assert all(c['state']=='DONE' for c in commands)
        # A temporary Defer is not a duplicate graph success. Retain attempts.
    incoming=sum(r['cohort']=='incoming' for r in results)
    expected_roles=Counter({'planning_select':n,'planning_plan':n})
    if burst:expected_roles=Counter({'planning_select':incoming,'planning_plan':incoming})
    if not approval:expected_roles.update({'review':n*spec.reviews,'report':n})
    if followup:expected_roles.update({'answer':n*2})
    expected_roles=+expected_roles
    assert Counter(m['role'] for m in s['models'])==expected_roles
    delay=cfg['delay_ms']/1000
    assert all(m['end']-m['start']>=delay-.02 for m in s['models'])
    handlers=s['event_handlers'];success=[h for h in handlers if h['error'] is None];defer=[h for h in handlers if h['error']]
    assert all(h['error'] in ('DeferEvent','_HandoffPending') for h in defer)
    assert len(success)==len({h['event_id'] for h in success})==(0 if approval else n*(spec.operations+1))
    assert all(c['state']=='DONE' and c['failure_attempts']==0 for c in db['commands'])
    assert len(db['commands'])==(0 if approval else n*(spec.operations+1))
    assert {c['command_id'] for c in db['commands']}=={h['command_id'] for h in success}
    if not approval:
        executions={r['execution_id'] for r in results}
        assert len(executions)==n and {h['execution_id'] for h in success}==executions
        assert not raw['mock']['tasks_failed'] and raw['mock']['pending']==0
        seen=[e for e in raw['mock']['executions'] if e['execution_id'] in executions]
        assert len(seen)==n and all(e['operations']==spec.operations and e['status']=='SUCCEEDED' for e in seen)
        calls=Counter(t['stage'] for t in raw['mock']['timeline'] if t['stage']!='event_published')
        assert calls==({'continue_accepted':n*(spec.operations-1),'finalize_accepted':n,'submit_accepted':incoming} if burst and incoming else
                       {'continue_accepted':n*(spec.operations-1),'finalize_accepted':n} if burst else
                       {'submit_accepted':n,'continue_accepted':n*(spec.operations-1),'finalize_accepted':n})
        assert all([o['step_id'] for o in r['observations']]==list(spec.step_ids) and r['report']['status']=='ready' for r in results)
    if followup:
        assert all(len(r['followups'])==2 for r in results)
        answers=[m for m in s['models'] if m['role']=='answer']
        assert all(m['analysis_reference'] and m['memory_reference'] for m in answers)
    events={t['event_id']:t['at'] for t in raw['mock']['timeline'] if t['stage']=='event_published'}
    waits=[(h['start']-events[h['event_id']])*1000 for h in success]
    user_ids={w['run_id'] for w in s['workers']}
    queue=[r['queue_ms'] for r in db['runs'] if r['run_id'] in user_ids]
    timings=[r['seconds'] for r in results]
    sql_count=sum(q['count'] for q in s['sql'])
    sql_ms=sum(q['total_ms'] for q in s['sql'])
    samples=[v for v in raw['samples'] if v['at']>=s['start']]
    # Invocations represent slot lifetime; sum is work-seconds, not E2E time.
    slot_seconds=sum(max(0,i['end']-max(i['start'],s['start'])) for i in s['invocations'])
    role_times={role:statistics.mean(m['end']-m['start'] for m in s['models'] if m['role']==role) for role in expected_roles}
    result={'architecture':cfg['architecture'],'scenario':scenario,'observation_profile':cfg.get('observation_profile','standard'),'users':n,'repeat':cfg['repeat'],
        'memory_mode':cfg['memory_mode'],'followup':followup,'total_capacity':cfg['total_capacity'],
        'user_capacity':cfg['user_capacity'],'event_capacity':cfg['event_concurrency'],'notify':cfg['notify'],
        'mean_seconds':statistics.mean(timings),'p95_seconds':quantile(timings),'makespan_seconds':raw['elapsed_seconds'],
        'batch_users_per_second':n/raw['elapsed_seconds'],'api_cpu_seconds':s['cpu_seconds'],
        'api_cpu_per_user_seconds':s['cpu_seconds']/n,'crud_sql_count':sql_count,'crud_sql_per_user':sql_count/n,
        'crud_sql_elapsed_ms':sql_ms,'sql_by_category':dict(Counter({k:sum(q['count'] for q in s['sql'] if q['category']==k) for k in {q['category'] for q in s['sql']}})),
        'claims':s['claims'],'empty_claims':s['empty_claims'],'shared_peak':s['peak_shared'],
        'slot_work_seconds':slot_seconds,'slot_utilization':slot_seconds/(raw['elapsed_seconds']*cfg['total_capacity']),
        'user_queue_mean_ms':statistics.mean(queue) if queue else None,'user_queue_p95_ms':quantile(queue),
        'event_publish_to_start_mean_ms':statistics.mean(waits) if waits else None,'event_publish_to_start_p95_ms':quantile(waits),
        'event_defer_attempts':len(defer),'command_attempts':Counter(c['attempt'] for c in commands),
        'model_calls':dict(expected_roles),'model_mean_seconds':role_times,
        'pool_acquire_p95_ms':quantile(s['acquires']),'loop_lag_p95_ms':quantile(s['loop_lag']),
        'loop_lag_max_ms':max(s['loop_lag'],default=0),
        'rss_peak_mib':max((v['rss_kib'] for v in samples),default=0)/1024,
        'db_connections_peak':max((v['db_connections'] for v in samples),default=0),
        'db_idle_transaction_peak':max((v['db_idle_transactions'] for v in samples),default=0),
        'successes':n,'validated':True}
    result['cohorts']={cohort:{'users':len(items),'mean_seconds':statistics.mean(r['seconds'] for r in items),'p95_seconds':quantile([r['seconds'] for r in items])}
        for cohort in {r['cohort'] for r in results} if (items:=[r for r in results if r['cohort']==cohort])}
    if followup:
        result['followup_mean_seconds']=[statistics.mean(r['followups'][i]['seconds'] for r in results) for i in range(2)]
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('captures',type=Path,nargs='+');p.add_argument('--output',required=True,type=Path);args=p.parse_args()
    rows=[]
    for folder in args.captures:
        for path in sorted(folder.rglob('raw.json')):
            row=analyze(json.loads(path.read_text()));rows.append({'capture':str(path.relative_to(folder)),**row})
    args.output.write_text(json.dumps(rows,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({'validated_trials':len(rows),'successful_users':sum(r['users'] for r in rows)}))
