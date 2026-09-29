"""Independent raw-evidence checks; do not import the report aggregation module."""
import argparse, gzip, json, math, statistics
from pathlib import Path


def load(path):
    return json.loads(gzip.decompress(path.read_bytes()) if path.suffix == '.gz' else path.read_bytes())


def covered(intervals):
    # Endpoint sweep is independent of analyze.py's merged-interval implementation.
    endpoints = sorted({point for pair in intervals for point in pair})
    return sum(b-a for a,b in zip(endpoints, endpoints[1:])
               if any(left <= a and b <= right for left,right in intervals))


def verify(folder, results):
    checks=[]
    for result in results:
        directory=folder/Path(result['raw']).parent
        raw=load(next(iter(directory.glob('raw.json*')))); server=raw['server']; n=result['users']
        assert result['source_commit']==raw['config']['commit']=='ea871a1'
        assert n==len(raw['scenarios']) and len(raw['stages'])==4*n
        expected=[statistics.mean(x['ms']/1000 for x in raw['scenarios']),
                  sum(x['queue_ms']/1000 for x in raw['database']['runs'])/n,
                  sum(x['ms']/1000 for x in server['workers'])/n]
        for key,value in zip(['total_s','queue_s','worker_s'],expected):
            assert math.isclose(result['per_user'][key]['mean'],value,abs_tol=1e-8)
        total_model=0
        for worker in server['workers']:
            rid=worker['run_id']
            events=[x for x in server['models'] if x['run_id']==rid]
            assert all(worker['start']<=x['start']<x['end']<=worker['end'] for x in events)
            total_model+=covered([(x['start'],x['end']) for x in events])
        assert math.isclose(total_model/n,result['per_user']['llm_s']['mean'],abs_tol=1e-8)
        assert math.isclose(expected[2]-total_model/n,result['per_user']['internal_s']['mean'],abs_tol=1e-8)
        assert sum(x['count'] for x in result['sql_by_actor'].values())==len(server['sql'])
        assert sum(x['kind']=='worker' for x in server['sql'])==322*n
        assert sum(x['kind']=='worker' for x in server['commits'])==61*n
        assert len({x['attempt_id'] for x in raw['stages']})==4*n
        assert len({x['run_id'] for x in raw['stages']})==n
        assert sum(x['operation']=='graph_build' for x in server['resources'])==1
        assert sum(x['operation']=='pool_construct' for x in server['resources'])==2
        assert len(raw['llm']['events'])==4*n and not any(x['stream'] for x in raw['llm']['events'])
        assert not any(x['forced_kill'] for x in load(directory/'shutdown.json').values())
        checks.append({'users':n,'status':'passed','complete_users':n,'attempts':4*n,
                       'model_calls':4*n,'worker_sql_per_user':322,'worker_commit_calls_per_user':61})
    return checks


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('folder',type=Path);p.add_argument('summary',type=Path)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    result=verify(a.folder,load(a.summary));a.output.write_text(json.dumps(result,indent=2)+'\n');print(result)
