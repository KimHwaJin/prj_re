"""Real retirement migrations discard seeded legacy rows and preserve active resources."""
import os
from pathlib import Path
import subprocess
import sys
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest
from sqlalchemy.engine import make_url

from dtest.infrastructure.database.models.agent_run_model import AgentRunModel
from dtest.infrastructure.database.models.message_model import MessageModel
from dtest.infrastructure.database.models.project_model import ProjectModel
from dtest.contracts.enums import AgentRunStatus, MessageType
from tests.api_service.test_user_identity_postgres import database_url, harness, initialize, add_user, add_session

ROOT = Path(__file__).resolve().parents[2]
RETIRED_TABLES = {'llm_runs', 'project_members', 'jupyter_servers',
                  'workflow_catalog', 'workflow_executions', 'workflow_adaptive_history', 'ew_commands', 'ew_outbox', 'ew_audit'}
RETIRED_COLUMNS = {'agent_message_id', 'interpreted_message_id', 'workflow_stage',
                   'request_payload', 'redis_key', 'redis_result', 'dispatched_at',
                   'agent_completed_at', 'redis_received_at', 'interpreted_at'}


@pytest.mark.asyncio
async def test_seeded_legacy_storage_is_removed_without_touching_active_resources(harness, database_url, tmp_path):
    h = harness
    await initialize(h)
    user = await add_user(h)
    sid = UUID(await add_session(h, user))
    async with h.factory() as db:
        project = await db.get(ProjectModel, UUID(user['default_project_id']))
        uid, pid = project.user_id, project.project_id
        message = MessageModel(session_id=sid, message_type=MessageType.USER, content_text='keep this message')
        db.add(message); await db.flush()
        run = AgentRunModel(session_id=sid, trigger_message_id=message.message_id,
                            idempotency_key='retirement-test', status=AgentRunStatus.SUCCESS,
                            agent_response={'answer': 'keep this result'})
        db.add(run); await db.commit()
        mid, rid = message.message_id, run.run_id
    raw = make_url(database_url).set(drivername='postgresql').render_as_string(hide_password=False)
    config = tmp_path/'config.yml'
    config.write_text('DATABASE_URL: '+database_url+'\nCHECKPOINT_DB_URI: '+raw+'\nMODEL_PROVIDER: mock\n'
                      'AGENT_WORKER_ENABLED: false\nEVENT_WORKER_ENABLED: false\nTASK_RECONCILER_ENABLED: false\n')
    config.chmod(0o600)
    env = {**os.environ, 'SERVICE_CONFIG_FILE':str(config), 'PYTHONPATH':str(ROOT/'src')}
    def migrate(file, direction, revision):
        result = subprocess.run([sys.executable,'-m','alembic','-c',file,direction,revision],
                                cwd=ROOT, env=env, text=True, capture_output=True, timeout=30)
        assert result.returncode == 0, result.stderr
    migrate('alembic.crud.ini','downgrade','20261005_0029')
    migrate('alembic.ini','upgrade','ew_0002')
    old_commands = []
    try:
        with psycopg.connect(raw) as db:
            db.execute("INSERT INTO project_members(project_id,user_id) VALUES (%s,%s)",(pid,uid))
            db.execute("INSERT INTO jupyter_servers(jupyter_server_id,name,endpoint) VALUES (%s,'retired','http://retired')",(uuid4(),))
            db.execute("""INSERT INTO llm_runs(run_id,session_id,trigger_message_id,provider,model_name,
                project_prompt_version,system_prompt_snapshot,request_messages_snapshot)
                VALUES (%s,%s,%s,'retired','retired',1,'','[]')""",(uuid4(),sid,mid))
            db.execute("UPDATE agent_runs SET redis_key='retired',request_payload='{}',workflow_stage='prepared' WHERE run_id=%s",(rid,))
            db.execute("""INSERT INTO workflow_catalog(catalog_id,workflow_id,revision,source_session_id,
                source_task_id,intent,workflow_status,workflow)
                VALUES (%s,'retired',1,%s,'retired','retired','ready','{}')""",(uuid4(),str(sid)))
            # Retiring the old ledger must not discard real queued/failed work,
            # including namespaces different from the launch configuration.
            for index, state in enumerate(('READY', 'FAILED')):
                ns, eid, event_id, command_id = f'old-namespace-{index}', uuid4(), uuid4(), uuid4()
                body = {'event_id': str(event_id), 'execution_id': str(eid),
                        'event_type': 'execution.completed', 'event_sequence': 1,
                        'schema_version': '1.0', 'occurred_at': '2026-10-06T00:00:00Z',
                        'payload': {'legacy_result': index}}
                db.execute("INSERT INTO ew_bindings(namespace,execution_id,session_id,task_id,created_by,updated_by) VALUES (%s,%s,%s,%s,'legacy','legacy')", (ns,eid,str(sid),str(uuid4())))
                db.execute("INSERT INTO ew_inbox(namespace,event_id,execution_id,sequence,event,state,created_by,updated_by) VALUES (%s,%s,%s,1,%s,'ROUTED','legacy','legacy')", (ns,event_id,eid,Jsonb(body)))
                db.execute("INSERT INTO ew_commands(namespace,command_id,event_id,execution_id,sequence,state,failure_attempts,last_error,created_by,updated_by) VALUES (%s,%s,%s,%s,1,%s,3,'legacy error','legacy','legacy')",(ns,command_id,event_id,eid,state))
                db.execute("INSERT INTO ew_outbox(namespace,command_id,created_by,updated_by) VALUES (%s,%s,'legacy','legacy')",(ns,command_id))
                db.execute("INSERT INTO ew_audit(namespace,command_id,action,reason,created_by,updated_by) VALUES (%s,%s,'retired','retired','legacy','legacy')",(ns,command_id))
                old_commands.append((ns,command_id,state,body))
        migrate('alembic.crud.ini','upgrade','head')
        migrate('alembic.ini','upgrade','head')
        with psycopg.connect(raw) as db:
            tables = {row[0] for row in db.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")}
            assert not (RETIRED_TABLES & tables)
            assert db.execute("SELECT to_regclass('workflow_adaptive_history_view')").fetchone() == (None,)
            for ns, command_id, state, body in old_commands:
                restored = db.execute("SELECT session_id,kind,state,failure_attempts,last_error,payload FROM agent_commands WHERE namespace=%s AND command_id=%s",(ns,command_id)).fetchone()
                assert restored[:5] == (sid,'executor_resume',state,3,'legacy error')
                assert restored[5]['event'] == body

            assert {'users','projects','sessions','messages','agent_runs','tasks','agent_commands',
                    'workflows','workflow_embeddings','store','store_migrations'} <= tables
            columns = {row[0] for row in db.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='agent_runs'")}
            assert not (RETIRED_COLUMNS & columns)
            assert {'run_id','public_run_id','agent_response','interrupt','task_id'} <= columns
            assert db.execute('SELECT content_text FROM messages WHERE message_id=%s',(mid,)).fetchone() == ('keep this message',)
            assert db.execute('SELECT agent_response FROM agent_runs WHERE run_id=%s',(rid,)).fetchone() == ({'answer':'keep this result'},)
            assert db.execute('SELECT user_id FROM projects WHERE project_id=%s',(pid,)).fetchone() == (uid,)
            assert db.execute("SELECT count(*) FROM pg_type WHERE typname IN ('llm_run_status','project_member_role')").fetchone() == (0,)
    finally:
        migrate('alembic.crud.ini','upgrade','head')
        migrate('alembic.ini','upgrade','head')
