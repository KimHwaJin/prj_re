"""Data-preserving upgrade/downgrade of representative pre-0021 histories."""
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
from sqlalchemy.engine import make_url
from tests.api_service.test_user_identity_postgres import database_url


def test_public_identity_backfill_preserves_existing_ids_and_rejects_cycles(database_url, tmp_path):
    root=Path(__file__).resolve().parents[2]
    dsn=make_url(database_url).set(drivername='postgresql').render_as_string(hide_password=False)
    config=tmp_path/'config.yml'
    config.write_text('database_url: '+database_url+'\nCHECKPOINT_DB_URI: '+dsn+'\n')
    env={**os.environ,'SERVICE_CONFIG_FILE':str(config),'APP_ENV':'dev','PYTHONPATH':str(root/'src')}
    def migrate(direction, revision, ok=True):
        proc=subprocess.run([sys.executable,'-m','alembic','-c','alembic.crud.ini',direction,revision],cwd=root,env=env,capture_output=True,text=True)
        assert (proc.returncode==0)==ok,proc.stderr
        return proc
    migrate('downgrade','20260929_0020')
    user,project,session,task,first,second,third,orphan,malformed,foreign_session,foreign= [uuid4() for _ in range(11)]
    try:
        with psycopg.connect(dsn) as db:
            db.execute('TRUNCATE users CASCADE')
            db.execute("INSERT INTO users(user_id,user_name,public_user_id,role) VALUES(%s,'legacy','legacy','user')",(user,))
            db.execute("INSERT INTO projects(project_id,user_id,project_name,is_default) VALUES(%s,%s,'legacy',true)",(project,user))
            for sid in (session,foreign_session):
                db.execute("INSERT INTO sessions(session_id,user_id,project_id,session_name) VALUES(%s,%s,%s,'legacy')",(sid,user,project))
            db.execute("INSERT INTO tasks(task_id,session_id,status,idempotency_key,trigger_type) VALUES(%s,%s,'waiting_input','legacy','analysis')",(task,session))
            for rid,tid,sid,metadata in [(first,task,session,{}),(second,task,session,{'checkpoint_run_id':str(first)}),
                                        (third,task,session,{'checkpoint_run_id':str(first)}),(orphan,None,session,{}),
                                        (malformed,None,session,{'checkpoint_run_id':'not-a-uuid'}),
                                        (foreign,None,foreign_session,{'checkpoint_run_id':str(first)})]:
                db.execute("INSERT INTO agent_runs(run_id,session_id,task_id,status,idempotency_key,metadata) VALUES(%s,%s,%s,'interrupted',%s,%s)",
                           (rid,sid,tid,str(rid),Jsonb(metadata)))
            db.execute('UPDATE tasks SET root_run_id=%s,checkpoint_run_id=%s WHERE task_id=%s',(first,first,task))
            before=db.execute('SELECT run_id,session_id,task_id,metadata FROM agent_runs ORDER BY run_id').fetchall()
        migrate('upgrade','head')
        with psycopg.connect(dsn) as db:
            mapping=dict(db.execute('SELECT run_id,public_run_id FROM agent_runs'))
            assert mapping=={first:first,second:first,third:first,orphan:orphan,malformed:malformed,foreign:foreign}
            assert db.execute('SELECT run_id,session_id,task_id,metadata FROM agent_runs ORDER BY run_id').fetchall()==before
            assert db.execute('SELECT root_run_id,checkpoint_run_id FROM tasks WHERE task_id=%s',(task,)).fetchone()==(first,first)
        migrate('downgrade','20260929_0020')
        with psycopg.connect(dsn) as db:
            assert db.execute('SELECT run_id,session_id,task_id,metadata FROM agent_runs ORDER BY run_id').fetchall()==before
            # An old task root pointing at a non-root chain is not guessed away.
            db.execute('UPDATE agent_runs SET metadata=%s WHERE run_id=%s',(Jsonb({'checkpoint_run_id':str(first)}),orphan))
            db.execute('UPDATE agent_runs SET metadata=%s WHERE run_id=%s',(Jsonb({'checkpoint_run_id':str(orphan)}),malformed))
        proc=migrate('upgrade','head',ok=False)
        assert 'Non-root historical Run reference' in proc.stderr
        with psycopg.connect(dsn) as db:
            assert db.execute('SELECT version_num FROM alembic_version').fetchone()[0]=='20260929_0020'
            db.execute('UPDATE agent_runs SET metadata=%s WHERE run_id=%s',(Jsonb({}),malformed))
        migrate('upgrade','head')
    finally:
        with psycopg.connect(dsn) as db:
            db.execute('TRUNCATE users CASCADE')
        migrate('upgrade','head')
