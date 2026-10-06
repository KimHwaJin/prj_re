"""Independent raw arithmetic and source audit; does not import the analyzer."""
import argparse
from collections import defaultdict
import gzip
import hashlib
import json
from pathlib import Path
import statistics


def main(output):
    manifest=json.loads((output/'manifest.json').read_text());rows=json.loads((output/'results.json').read_text())
    indexed={row['capture']:row for row in rows};groups=defaultdict(list);sources={}
    count=0
    for item in manifest:
        packed=(output/'raw'/item['capture']).read_bytes()
        assert hashlib.sha256(packed).hexdigest()==item['gzip_sha256']
        plain=gzip.decompress(packed);assert hashlib.sha256(plain).hexdigest()==item['raw_sha256']
        raw=json.loads(plain);row=indexed[item['capture']];n=raw['config']['users']
        seconds=[r['seconds'] for r in raw['results']]
        assert len(seconds)==n and raw['passed'] and not raw['errors']
        mean=sum(seconds)/n
        ordered=sorted(seconds);rank=(n-1)*.95;lo=int(rank)
        p95=ordered[lo]+(ordered[min(lo+1,n-1)]-ordered[lo])*(rank-lo)
        for actual,expected in ((row['mean_seconds'],mean),(row['p95_seconds'],p95),
            (row['batch_users_per_second'],n/raw['elapsed_seconds']),
            (row['crud_sql_per_user'],sum(x['count'] for x in raw['server']['sql'])/n)):
            assert abs(actual-expected)<1e-8
        config=raw['config'];arch=config['architecture']
        assert config['total_capacity']==20 and config['delay_ms']==5000
        assert config['user_capacity']==(16 if arch=='split' else 20) and config['event_concurrency']==4
        assert config['source_commit']==('faca5b1' if arch=='split' else '32f443e')
        hashes=config['source_sha256']
        if arch in sources:assert sources[arch]==hashes,'Runtime source changed between trials'
        else:sources[arch]=hashes
        if not config['followup'] and config['notify']=='on':groups[(config['scenario'],n,arch)].append(seconds)
        count+=n
    summary=json.loads((output/'summary.json').read_text())
    assert len(groups)==len(summary)
    for row in summary:
        values=groups[(row['scenario'],row['users'],row['architecture'])]
        assert row['repeats']==len(values)==(3 if row['users']==50 else 1)
        trial_means=[sum(batch)/len(batch) for batch in values]
        expected_std=statistics.stdev(trial_means) if len(trial_means)>1 else None
        assert (row['mean_std_seconds'] is None if expected_std is None else abs(row['mean_std_seconds']-expected_std)<1e-8)
        flattened=[v for batch in values for v in batch]
        assert abs(row['mean_seconds']-sum(flattened)/len(flattened))<1e-8
    assert len(rows)==59 and count==1722,'Missing planned trial/cohort'
    expected=set()
    for scenario in ('approval','executor','result_burst','mixed'):
        for users in (1,10,30,50):
            for repeat in range(1,4 if users==50 else 2):
                for arch in ('split','common'):
                    expected.add((scenario,users,repeat,arch,'manual',False,'on'))
    for mode in ('manual','auto_context'):
        for users in (1,10):
            for arch in ('split','common'):
                expected.add(('executor',users,1,arch,mode,True,'on'))
    expected.update(('approval',50,repeat,'common','manual',False,'off') for repeat in (1,2,3))
    keys=[tuple(row[field] for field in ('scenario','users','repeat','architecture','memory_mode','followup','notify')) for row in rows]
    assert len(set(keys))==len(keys) and set(keys)==expected,'Missing or duplicate planned condition'
    agent_files={name for name in sources['split'] if name.startswith('src/dtest.agent_service/')}
    assert agent_files=={name for name in sources['common'] if name.startswith('src/dtest.agent_service/')}
    assert all(sources['split'][name]==sources['common'][name] for name in agent_files)
    result={'assessment':'Share with caveats','raw_hashes_and_arithmetic_verified':True,
        'all_planned_trials_present':True,'trials':len(rows),'user_scenarios':count,
        'agent_service_files_identical':len(agent_files),'equal_total_capacity':20,
        'p95_definition':'Trial-specific user percentile; repeat range preserved, not a pooled p95',
        'caveats':['Single local API process, finite synchronized arrivals, no Kubernetes quota/HPA',
            'All in-window model calls use 5-second transport fixture; actual provider/SSO/Tool compute excluded',
            'API CPU and CRUD SQL only; no database, Redis, client or Executor process CPU',
            'Follow-up report is grounded Markdown response; separate Artifact rewrite/registration excluded',
            'Burst setup models use zero delay and are excluded from timing/cost window']}
    (output/'independent-verification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('output',type=Path);main(p.parse_args().output)
