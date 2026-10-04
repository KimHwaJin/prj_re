"""Serve the standalone test HTML on the real app, loopback only.

Normal mode uses the supplied service settings and corporate SSO unchanged.
--local-fixtures replaces only the employee verdict/model transport; it requires
isolated test databases. --temporary-db owns an ephemeral Docker PostgreSQL.
Nothing here is imported by the production application.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import asyncio
import json
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

from fastapi.responses import HTMLResponse
import uvicorn

from cookie_auth import install_employee_fixture
from verify_api_contract_flow import settings_for_test, migrate
from service_settings import load_settings
from service_bootstrap import create_app

ROOT = Path(__file__).resolve().parents[2]
HTML = ROOT / 'tools/test-console/index.html'


class ConsoleServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self):
        # Do not re-raise SIGINT inside asyncio.run before diagnostic cleanup.
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self.handle_exit, sig, None)
        try:
            yield
        finally:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(sig)


def command(*args):
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[-1500:])
    return result.stdout.strip()


def temporary_database(args, name):
    command('docker', 'run', '--rm', '-d', '--name', name,
            '-e', 'POSTGRES_USER=console_test', '-e', 'POSTGRES_PASSWORD=console_test_only',
            '-p', f'127.0.0.1:{args.db_port}:5432', 'postgres:17')
    for _ in range(60):
        try:
            command('docker', 'exec', name, 'pg_isready', '-h', '127.0.0.1', '-U', 'console_test')
            break
        except RuntimeError:
            time.sleep(.25)
    else:
        raise RuntimeError('Temporary PostgreSQL did not become ready')
    for db in ('agentic_runtime_test', 'agentic_checkpoint_test'):
        command('docker', 'exec', name, 'createdb', '-h', '127.0.0.1', '-U', 'console_test', db)
    prefix = f'postgresql+asyncpg://console_test:console_test_only@127.0.0.1:{args.db_port}/'
    return {'DATABASE_URL': prefix+'agentic_runtime_test',
            'CHECKPOINT_DB_URI': prefix.replace('+asyncpg', '')+'agentic_checkpoint_test'}


async def serve(args, config, namespace):
    if args.local_fixtures:
        config = settings_for_test(config, namespace, args.port)
        config.update(SSO_COOKIE_NAME=f'dtest_test_console_{args.port}', SSO_ALLOWED_RETURN_ROOTS=['/test-console'],
                      EXECUTOR_SUBMIT_ENABLED=args.executor=='real')
        migrate(config)
        from verify_api_contract_flow import install_model_fixture
        install_model_fixture({'model_delay_ms':args.model_delay_ms}, {'models':[]}, lambda:True)
    settings = load_settings(config=config, environ={})
    app = create_app(settings)
    if args.local_fixtures:
        install_employee_fixture(app, namespace)
        if args.fixture_admin:
            from bootstrap_admin import bootstrap
            await bootstrap(namespace, 'Test console administrator')
    # Added to this diagnostic app instance only, never the production router.
    @app.get('/test-console', include_in_schema=False)
    async def console():
        runtime = {'apiBase': f'http://127.0.0.1:{args.port}{settings.api.api_v1_prefix}',
                   'fixture': args.local_fixtures, 'executor': settings.agent.executor_submit_enabled}
        text = HTML.read_text().replace('<script id="console-app">',
            '<script>window.TEST_CONSOLE_CONFIG='+json.dumps(runtime).replace('<','\\u003c')+';</script>\n<script id="console-app">')
        return HTMLResponse(text, headers={'Cache-Control':'no-store'})
    print(f'Console: http://127.0.0.1:{args.port}/test-console', flush=True)
    if args.local_fixtures:
        print('TEST ONLY: SSO employee verdict + fixed model transport. '
              f'Executor submit: {args.executor}. Fixture role: {"admin" if args.fixture_admin else "user"}.', flush=True)
        print('Prefix [answer] for fixed-model answer tests; other input selects the quality plan.', flush=True)
    server = ConsoleServer(uvicorn.Config(app, host='127.0.0.1', port=args.port,
                                          log_level='warning', access_log=False))
    try:
        await server.serve()
    finally:
        if args.local_fixtures:
            from agent_service.agents.analysis.planning import runtime
            for client in getattr(runtime, '_benchmark_fixture_clients', []):
                await client.aclose()
            from redis.asyncio import Redis
            redis = Redis.from_url(config['REDIS_URL'])
            try:
                await redis.xgroup_destroy(settings.worker.executor_event_stream, settings.worker.event_group)
                keys = [key async for key in redis.scan_iter(match=namespace+':sso:*')]
                if keys:
                    await redis.delete(*keys)
            finally:
                await redis.aclose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settings-file', type=Path, help='Private flat service JSON, not .env')
    parser.add_argument('--local-fixtures', action='store_true')
    parser.add_argument('--fixture-admin', action='store_true', help='Bootstrap a synthetic admin, temporary fixture DB only')
    parser.add_argument('--temporary-db', action='store_true', help='Requires fixtures; owned Docker PG17, removed on exit')
    parser.add_argument('--port', type=int, default=18100)
    parser.add_argument('--db-port', type=int, default=53601)
    parser.add_argument('--redis-url', default='redis://127.0.0.1:6379/0')
    parser.add_argument('--executor-base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--executor-shared-root', type=Path)
    parser.add_argument('--executor', choices=['off','real'], default='off', help='Fixture mode only; normal mode uses config')
    parser.add_argument('--model-delay-ms', type=int, default=300)
    args = parser.parse_args()
    if args.fixture_admin and not (args.local_fixtures and args.temporary_db):
        parser.error('--fixture-admin requires --local-fixtures --temporary-db')
    if args.temporary_db and (not args.local_fixtures or args.settings_file):
        parser.error('--temporary-db requires --local-fixtures and no --settings-file')
    if not args.temporary_db and not args.settings_file:
        parser.error('Supply --settings-file or --local-fixtures --temporary-db')
    if args.model_delay_ms<0 or not all(1<=p<=65535 for p in (args.port,args.db_port)):
        parser.error('Invalid delay or port')
    namespace = 'test-console-'+uuid4().hex[:12]
    container = 'dtest-'+namespace
    workspace = tempfile.TemporaryDirectory(prefix=namespace+'-') if args.temporary_db else None
    try:
        if args.temporary_db:
            config = temporary_database(args,container)
            config.update(REDIS_URL=args.redis_url, EXECUTOR_BASE_URL=args.executor_base_url,
                WORKFLOW_STORAGE_ROOT=str(Path(workspace.name)/'workflows'),
                ANALYSIS_DATASETS={'default-nce':{'title':'NCE local sample','scope':'GLOBAL',
                    'runtime_path':'/workspace/pv/default_data/df_nce_long_format.parquet'}})
        else:
            config = json.loads(args.settings_file.read_text())
        if args.executor_shared_root:
            config['EXECUTOR_SHARED_RESULT_ROOT']=str(args.executor_shared_root.resolve())
        if args.local_fixtures and args.executor=='real' and not config.get('EXECUTOR_SHARED_RESULT_ROOT'):
            parser.error('Actual Executor requires --executor-shared-root or EXECUTOR_SHARED_RESULT_ROOT')
        asyncio.run(serve(args,config,namespace))
    finally:
        if args.temporary_db:
            # Exact diagnostic-owned container only; existing Compose is untouched.
            subprocess.run(['docker','stop',container],capture_output=True,text=True)
        if workspace:
            workspace.cleanup()


if __name__=='__main__':
    main()
