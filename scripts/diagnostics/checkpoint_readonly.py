"""Read-only Frodo checkpoint audit. Prints metadata, never prompts or credentials.

Run: PYTHONPATH=src .venv/bin/python scripts/diagnostics/checkpoint_readonly.py
"""
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import asyncpg
from sqlalchemy.engine import make_url
from agent_config import load_agent_settings
from config import settings


def obj(value):
    return json.loads(value) if isinstance(value, str) else value


async def connect(url):
    u = make_url(url)
    return await asyncpg.connect(host=u.host, port=u.port or 5432, user=u.username,
                                password=u.password, database=u.database, ssl=False,
                                timeout=5, command_timeout=15,
                                server_settings={'default_transaction_read_only': 'on',
                                                 'application_name': 'codex-checkpoint-readonly'})


async def main():
    agent = load_agent_settings()
    urls = {'chat_app': settings.database_url, 'agent': agent.checkpoint_db_uri}
    report = {'observed_at': datetime.now(timezone.utc).isoformat(), 'databases': {}, 'sessions': {}}
    conn = await connect(urls['chat_app'])
    try:
        async with conn.transaction(readonly=True):
            report['failure_summary'] = [dict(r) for r in await conn.fetch('''
                SELECT status::text, jsonb_typeof(input) AS input_type,
                       jsonb_typeof(command) AS command_type, count(*) AS count
                FROM agent_runs WHERE failure->>'message'='user_request is required'
                GROUP BY 1,2,3''')]
            failed = await conn.fetch('''SELECT run_id,session_id,created_at,started_at,completed_at,
                    attempt_count, metadata->>'resume_run_id' AS resume_run_id
                FROM agent_runs WHERE failure->>'message'='user_request is required'
                ORDER BY created_at DESC LIMIT 12''')
            report['recent_failed_runs'] = [dict(r) for r in failed]
            sessions = list(dict.fromkeys(r['session_id'] for r in failed))
            for session in sessions:
                runs = await conn.fetch('''SELECT run_id,status::text,created_at,started_at,completed_at,
                    attempt_count,failure->>'message' AS error,jsonb_typeof(input) AS input_type,
                    jsonb_typeof(command) AS command_type,
                    metadata->>'resume_run_id' AS resume_run_id
                    FROM agent_runs WHERE session_id=$1 ORDER BY created_at''', session)
                logs = await conn.fetch('''SELECT l.node,l.event,l.kind,count(*) AS count
                    FROM agent_run_logs l JOIN agent_runs r USING(run_id)
                    WHERE r.session_id=$1 GROUP BY 1,2,3''', session)
                report['sessions'][str(session)] = {'runs':[dict(r) for r in runs],
                                                   'log_kinds':[dict(r) for r in logs]}
    finally:
        await conn.close()
    for name, url in urls.items():
        conn = await connect(url)
        try:
            async with conn.transaction(readonly=True):
                identity = dict(await conn.fetchrow('SELECT current_database() AS db,current_schema() AS schema,current_setting(\'transaction_read_only\') AS readonly'))
                identity['checkpoint_summary'] = dict(await conn.fetchrow('''SELECT count(*) AS rows,
                    count(DISTINCT thread_id) AS threads, min(checkpoint->>'ts') AS first_ts,
                    max(checkpoint->>'ts') AS last_ts FROM checkpoints'''))
                identity['active_checkpoint_clients'] = [dict(r) for r in await conn.fetch('''
                    SELECT application_name, state, count(*) AS count FROM pg_stat_activity
                    WHERE datname=current_database() AND pid<>pg_backend_pid()
                      AND query ILIKE '%checkpoint%' GROUP BY 1,2''')]
                report['databases'][name] = identity
                for thread, detail in report['sessions'].items():
                    rows = await conn.fetch('''SELECT checkpoint_ns,checkpoint_id,parent_checkpoint_id,
                            checkpoint->>'ts' AS ts,metadata->>'step' AS step, metadata->>'source' AS source,
                            checkpoint->'channel_values' AS values,checkpoint->'channel_versions' AS versions,
                            metadata AS metadata
                        FROM checkpoints WHERE thread_id=$1 ORDER BY checkpoint_id DESC LIMIT 8''', thread)
                    snapshots = []
                    for r in rows:
                        vals, versions, meta = obj(r['values']) or {}, obj(r['versions']) or {}, obj(r['metadata']) or {}
                        writes = await conn.fetch('''SELECT channel,type,blob FROM checkpoint_writes
                            WHERE thread_id=$1 AND checkpoint_ns=$2 AND checkpoint_id=$3''', thread, r['checkpoint_ns'], r['checkpoint_id'])
                        snapshots.append({'id':r['checkpoint_id'],'ns':r['checkpoint_ns'],'ts':r['ts'],
                            'step':r['step'],'source':r['source'],'inline_keys':list(vals),
                            'user_request_length':len(str(vals.get('user_request') or '')),
                            'version_keys':list(versions), 'metadata_keys':list(meta),
                            'metadata_run_id':meta.get('run_id'),
                            'writes': [{'channel':w['channel'],'type':w['type'],
                                'user_request_error':b'user_request is required' in (w['blob'] or b'')}
                                for w in writes]})
                    detail[name] = snapshots
        finally:
            await conn.close()
    out = Path('var/diagnostics/checkpoint-readonly.json')
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, default=str, indent=2, ensure_ascii=False))
    print(json.dumps({'observed_at':report['observed_at'],'failure_summary':report['failure_summary'],
                      'databases':report['databases'],'sample_sessions':len(report['sessions'])},default=str,ensure_ascii=False))
    for thread, detail in report['sessions'].items():
        compact = {'session':thread}
        for db in urls:
            rows=detail.get(db, [])
            latest = rows[0] if rows else {}
            compact[db]={'rows_read':len(rows),'latest_ts':latest.get('ts'),
                         'latest_step':latest.get('step'),
                         'has_request_in_sample':any(r['user_request_length']>0 or 'user_request' in r['version_keys'] for r in rows),
                         'has_error_in_sample':any(w['user_request_error'] for r in rows for w in r['writes'])}
        print(json.dumps(compact,ensure_ascii=False))
    print('Report:',out)


if __name__ == '__main__':
    asyncio.run(main())
