"""Resume a pre-move PostgreSQL checkpoint using the post-move source tree.

Uses explicit source snapshots, mock LLM/data, no Executor calls, and a dedicated
local database named boundary_checkpoint_test. Does not drop schemas or tables.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4
from urllib.parse import urlparse

CHILD = '''
import asyncio, json, sys
from agent_config import load_agent_settings
from agent_service.agents.analysis.dependencies import create_llm_dependencies
from agent_service.agents.analysis.graph import build_analysis_workflow_graph
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command
settings=load_agent_settings({'MODEL_PROVIDER':'mock','DATA_MOCK':'true',
    'DEMO_ARTIFACTS_ENABLED':'false','EXECUTOR_SUBMIT_ENABLED':'false','EXECUTOR_SOURCE_TYPE':'INLINE'})
async def main():
    async with AsyncPostgresSaver.from_conn_string(sys.argv[1]) as saver:
        await saver.setup()
        graph=build_analysis_workflow_graph(create_llm_dependencies(settings),settings,checkpointer=saver)
        cfg={'configurable':{'thread_id':sys.argv[2]}}
        if sys.argv[3]=='before':
            state=await graph.ainvoke({'user_request':'package transition', 'session_id':sys.argv[2],
                'user_id':'user','project_id':'project'},cfg,durability='sync')
            for answer in ['mock',{'objective':'EDA'}]:
                state=await graph.ainvoke(Command(resume=answer),cfg,durability='sync')
            assert state['__interrupt__']
        else:
            snapshot=await graph.aget_state(cfg)
            assert snapshot.next and snapshot.values['user_request']=='package transition'
            for answer in [{'candidate_number':1},{'approved':True}]:
                state=await graph.ainvoke(Command(resume=answer),cfg,durability='sync')
            assert state['executor_submit_response']['skipped'] and state['execution_steps']
        snapshot=await graph.aget_state(cfg)
        print(json.dumps({'phase':sys.argv[3],'next':list(snapshot.next),
            'steps':len(snapshot.values.get('execution_steps',[])),
            'nodes':sorted(graph.get_graph().nodes),
            'edges':sorted((e.source,e.target) for e in graph.get_graph().edges)}))
asyncio.run(main())
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before-source',type=Path,required=True)
    parser.add_argument('--after-source',type=Path,required=True)
    parser.add_argument('--database-url',required=True)
    args=parser.parse_args()
    parsed=urlparse(args.database_url)
    if parsed.hostname not in {'127.0.0.1','localhost'} or parsed.path!='/boundary_checkpoint_test':
        parser.error('Only local boundary_checkpoint_test is allowed')
    thread=str(uuid4());results=[]
    for phase,source in [('before',args.before_source),('after',args.after_source)]:
        result=subprocess.run([sys.executable,'-c',CHILD,args.database_url,thread,phase],
            cwd=source,env={**os.environ,'PYTHONPATH':str(source.resolve()/'src')},
            text=True,capture_output=True,timeout=60)
        if result.returncode:
            raise RuntimeError(f'{phase} failed: {result.stderr}')
        results.append(json.loads(result.stdout.splitlines()[-1]))
    assert results[0]['nodes']==results[1]['nodes']
    assert results[0]['edges']==results[1]['edges']
    print(json.dumps({'postgres_checkpoint_resumed':True,'graph_nodes_edges_unchanged':True,
        'before_next':results[0]['next'],'after_next':results[1]['next'],
        'mock_execution_steps':results[1]['steps']},indent=2))

if __name__=='__main__':main()
