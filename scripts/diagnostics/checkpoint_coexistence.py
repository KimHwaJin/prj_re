"""Isolated local PostgreSQL reproduction; real graph/service, stub model and bridge.

Creates two uniquely named disposable databases on localhost:15432, then drops
only those databases. Does not invoke the running API or any external service.
Run: PYTHONPATH=src:. .venv/bin/python scripts/diagnostics/checkpoint_coexistence.py
"""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import json
import logging
from pathlib import Path
import traceback
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from app.services import agent_graph_service as svc
from app.services.workflow_persistence import NullWorkflowStore
from app.test.test_user_agent_graph import dependencies, settings as fixture_settings, interrupt_payload

logging.disable(logging.CRITICAL)
ROOT = Path(__file__).resolve().parents[2]
RESULTS = []
CONFIG = {'db': 'agent'}
URLS = {}


@asynccontextmanager
async def no_bridge(*args, **kwargs):
    yield SimpleNamespace(bindings=None)


async def graph_only(graph, graph_input, **kwargs):
    return await graph.ainvoke(graph_input, config=kwargs['config'])


async def invoke(thread, first=False):
    if first:
        return await svc.ainvoke_user_turn(None,user_id=uuid4(),project_id=uuid4(),session_id=thread,
                                           run_id=uuid4(),user_request='Predict wafer failures')
    return await svc.ainvoke_resume(None,user_id=uuid4(),session_id=thread,
                                    checkpoint_run_id=uuid4(),agent_run_id=uuid4(),command='mock')


async def capture(case, thread, first=False, expect_error=False):
    try:
        state = await invoke(thread, first)
        row={'case':case,'db':CONFIG['db'],'result':'interrupt',
             'has_user_request':bool(state.get('user_request')),
             'interrupt':interrupt_payload(state)}
        assert not expect_error and state.get('__interrupt__') and state.get('user_request')
    except ValueError as exc:
        if not expect_error or str(exc) != 'user_request is required':
            raise
        frames=traceback.extract_tb(exc.__traceback__)
        row={'case':case,'db':CONFIG['db'],'result':'expected_error','message':str(exc),
             'raise_site':next((f'{Path(f.filename).name}:{f.lineno}:{f.name}' for f in reversed(frames)
                                if f.name=='receive_request'),None)}
        assert row['raise_site']
    RESULTS.append(row)
    print(json.dumps(row),flush=True)


async def run_cases():
    fixture=fixture_settings(artifacts_enabled=False)
    with patch.object(svc,'settings',SimpleNamespace(graph_checkpointer='postgres')), \
         patch.object(svc,'runtime',svc.AgentGraphRuntime()), \
         patch.object(svc,'load_agent_settings',side_effect=lambda:replace(fixture,checkpoint_db_uri=URLS[CONFIG['db']],checkpoint_setup_on_start=False)), \
         patch.object(svc,'ainvoke_with_crud_message_persistence',graph_only), \
         patch('app.agents.orchestration.dependencies.create_llm_dependencies',return_value=dependencies()[0]), \
         patch('app.agent_worker.api_bridge.ApiWorkerBridge',no_bridge), \
         patch('app.services.workflow_persistence.workflow_store_from_environment',return_value=NullWorkflowStore()):
        # Both databases already have all checkpoint tables before any request.
        CONFIG['db']='chat_app'
        await capture('populate_other_database',uuid4(),first=True)
        CONFIG['db']='agent'
        t=uuid4()
        await capture('both_tables_exist.initial_agent',t,first=True)
        await capture('both_tables_exist.resume_agent',t)
        CONFIG['db']='chat_app'
        t=uuid4()
        await capture('all_in_chat_app.initial',t,first=True)
        await capture('all_in_chat_app.resume',t)
        for start,end in [('agent','chat_app'),('chat_app','agent')]:
            t=uuid4()
            CONFIG['db']=start
            await capture(f'split_{start}_to_{end}.initial',t,first=True)
            CONFIG['db']=end
            await capture(f'split_{start}_to_{end}.resume',t,expect_error=True)
            CONFIG['db']=start
            await capture(f'split_{start}_to_{end}.recover_original',t)
        await capture('same_database_wrong_thread',uuid4(),expect_error=True)


async def main():
    values={}
    for line in (ROOT/'.env.local').read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            k,v=line.split('=',1); values[k]=v.strip().strip("'\"")
    admin=make_conninfo(host='127.0.0.1',port=values.get('LOCAL_POSTGRES_PORT','15432'),
                       user='dtest',password=values['LOCAL_POSTGRES_PASSWORD'],dbname='postgres',sslmode='disable')
    created=[]
    async with await psycopg.AsyncConnection.connect(admin,autocommit=True) as conn:
        try:
            suffix=uuid4().hex[:10]
            for role in ['chat_app','agent']:
                name='diag_coexist_'+role+'_'+suffix
                await conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
                created.append(name)
                URLS[role]=make_conninfo(admin,dbname=name)
                async with AsyncPostgresSaver.from_conn_string(URLS[role]) as saver:
                    await saver.setup()
            await run_cases()
        finally:
            for name in created:
                await conn.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
    out=ROOT/'var/diagnostics/checkpoint-coexistence.json'
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps({'cases':RESULTS,'temporary_databases_removed':created},indent=2))
    print(f'PASS: {len(RESULTS)} steps, disposable databases removed',flush=True)


if __name__=='__main__':
    asyncio.run(main())
