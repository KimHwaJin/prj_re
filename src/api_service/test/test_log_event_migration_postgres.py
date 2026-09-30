"""Preserve legacy event IDs/sequences/payloads while establishing log links."""
import os
from pathlib import Path
import subprocess
import sys
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest
from sqlalchemy.engine import make_url

from api_service.services.agent_run_log_service import AgentRunLogService
from api_service.test.test_user_identity_postgres import database_url, harness, add_session
from api_service.test.test_run_cleanup_postgres import runtime, enqueue
from api_service.test.test_log_event_atomicity_postgres import arguments


@pytest.mark.asyncio
async def test_upgrade_pairs_occurrences_repairs_missing_and_preserves_history(runtime, database_url, tmp_path):
    h = runtime
    rid = UUID((await enqueue(h))['id'])
    other = UUID((await enqueue(h, await add_session(h, h.user)))['id'])
    root = Path(__file__).resolve().parents[3]
    dsn = make_url(database_url).set(drivername='postgresql').render_as_string(hide_password=False)
    config = tmp_path/'config.yml'
    config.write_text('service:\n  database_url: '+database_url+'\n  checkpoint_db_uri: '+dsn+'\n')
    env = {**os.environ, 'SERVICE_CONFIG_FILE': str(config), 'APP_ENV': 'dev', 'PYTHONPATH': str(root/'src')}
    def migrate(direction, revision):
        proc = subprocess.run([sys.executable, '-m', 'alembic', '-c', 'alembic.crud.ini', direction, revision],
                              cwd=root, env=env, capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
    def history(db):
        return db.execute('SELECT task_event_id,task_id,run_id,sequence,event_type,payload,created_at '
                          'FROM task_events ORDER BY task_id,sequence').fetchall()
    migrate('downgrade', '20260929_0022')
    logs = {}
    bodies = {}
    try:
        with psycopg.connect(dsn) as db:
            tid, baseline = db.execute('SELECT t.task_id,t.last_event_sequence FROM tasks t JOIN agent_runs r '
                                     'ON r.task_id=t.task_id WHERE r.run_id=%s', (rid,)).fetchone()
            for index, (key, payload) in enumerate([
                ('normal', {'result': 'normal'}), ('missing', {'result': 'missing'}),
                ('repeat1', {'result': 'same'}), ('repeat2', {'result': 'same'}), ('repeat3', {'result': 'same'}),
            ]):
                log_id = uuid4()
                logs[key] = log_id
                body = {'agent_name': None, 'node': 'node', 'event': 'result', 'kind': 'agent_run_log', 'payload': payload}
                bodies[key] = body
                db.execute("INSERT INTO agent_run_logs(log_id,run_id,event_key,agent_name,node,event,kind,payload,created_at) "
                           "VALUES(%s,%s,%s,NULL,'node','result','agent_run_log',%s,'2026-09-01'::timestamptz + %s * interval '1 second')",
                           (log_id, rid, key, Jsonb(payload), index))
            # A log in another Run must never match the first Run's equal payload.
            db.execute("INSERT INTO agent_run_logs(log_id,run_id,event_key,node,event,kind,payload) "
                       "VALUES(%s,%s,'foreign','node','result','agent_run_log',%s)",
                       (uuid4(), other, Jsonb({'result': 'normal'})))
            for offset, body in enumerate([bodies['normal'], bodies['repeat1'], bodies['repeat2'], {'unmatched': True}], 1):
                db.execute("INSERT INTO task_events(task_event_id,task_id,run_id,sequence,event_type,payload) "
                           "VALUES(%s,%s,%s,%s,'agent.event',%s)", (uuid4(), tid, rid, baseline+offset, Jsonb(body)))
            db.execute('UPDATE tasks SET last_event_sequence=%s WHERE task_id=%s', (baseline+4, tid))
            before = history(db)
        migrate('upgrade', 'head')
        with psycopg.connect(dsn) as db:
            assert history(db) == before
            linked = [row[0] for row in db.execute('SELECT agent_run_log_id FROM task_events WHERE '
                      "run_id=%s AND event_type='agent.event' ORDER BY sequence", (rid,))]
            assert linked == [logs['normal'], logs['repeat1'], logs['repeat2'], None]
        for key in logs:
            async with h.factory() as db:
                await AgentRunLogService.create(db, **arguments(rid, key))
        async with h.factory() as db:
            await AgentRunLogService.create(db, **arguments(other, 'foreign'))
        with psycopg.connect(dsn) as db:
            # Exactly two missing first-Run events and one other-Run event added.
            after = history(db)
            assert len(after) == len(before) + 3
            old_ids = {row[0] for row in before}
            assert [row for row in after if row[0] in old_ids] == before
            assert db.execute('SELECT count(*) FROM task_events WHERE agent_run_log_id IS NOT NULL').fetchone()[0] == 6
            assert db.execute('SELECT last_event_sequence FROM tasks WHERE task_id=%s', (tid,)).fetchone()[0] == baseline + 6
        migrate('downgrade', '20260929_0022')
        with psycopg.connect(dsn) as db:
            assert history(db) == after
        migrate('upgrade', 'head')
        with psycopg.connect(dsn) as db:
            assert history(db) == after
            assert db.execute('SELECT count(*) FROM task_events WHERE agent_run_log_id IS NOT NULL').fetchone()[0] == 6
    finally:
        migrate('upgrade', 'head')
