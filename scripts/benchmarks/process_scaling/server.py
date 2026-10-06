"""Uvicorn worker factory; instrumentation only, selected service source unchanged."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import runpy
import socket
import sys

import uvicorn
from fastapi import FastAPI


def create_app():
    config = Path(os.environ['BENCH_CONFIG'])
    cfg = json.loads(config.read_text())
    previous_argv = sys.argv
    sys.argv = ['server.py', '--source', os.environ['BENCH_SOURCE'], '--config', str(config)]
    try:
        scope = runpy.run_path(str(Path(__file__).parents[1] / 'runtime_profile/server.py'), run_name='instrumented_worker')
    finally:
        sys.argv = previous_argv
    app = scope['app']
    control = FastAPI()
    for route, endpoint in [('/_bench/ready', scope['ready']), ('/_bench/progress', scope['progress']), ('/_bench/metrics', scope['metrics'])]:
        control.add_api_route(route, endpoint)
    control.add_api_route('/_bench/reset', scope['clear'], methods=['POST'])
    previous_life = app.router.lifespan_context

    @asynccontextmanager
    async def life(application):
        async with previous_life(application) as state:
            from sqlalchemy import text
            from api_service.infrastructure.database import get_session_factory
            from api_service.resources.users import UserService
            from api_service.schemas.user_schema import UserCreate
            async def warm():
                async with get_session_factory()() as db:
                    await db.execute(text('SELECT pg_sleep(.01)'))
            await asyncio.gather(*(warm() for _ in range(cfg['pool'])))
            # Service bootstrap uses its own management lock: concurrent calls
            # return the same administrator without racing the unique constraint.
            async with get_session_factory()() as db:
                await UserService.bootstrap_admin(db, UserCreate(user_id='admin', user_name='Benchmark admin', role='admin'))
            sock = socket.socket()
            sock.bind(('127.0.0.1', 0))
            sock.listen(128)
            server = uvicorn.Server(uvicorn.Config(control, log_level='error', lifespan='off', timeout_graceful_shutdown=2))
            # This auxiliary localhost listener only reads/resets this worker's
            # metrics. User traffic uses the real shared Uvicorn worker socket.
            server.capture_signals = __import__('contextlib').nullcontext
            control_task = asyncio.create_task(server.serve(sockets=[sock]))
            while not server.started:
                if control_task.done():
                    await control_task
                await asyncio.sleep(.01)
            sampler = asyncio.create_task(scope['sampler']())
            registration = Path(cfg['registry']) / f'{os.getpid()}.json'
            registration.write_text(json.dumps({'pid': os.getpid(), 'port': sock.getsockname()[1]}))
            try:
                yield state
            finally:
                sampler.cancel()
                await asyncio.gather(sampler, return_exceptions=True)
                server.should_exit = True
                await control_task
                sock.close()
    app.router.lifespan_context = life
    return app


if __name__ == '__main__':
    cfg = json.loads(Path(os.environ['BENCH_CONFIG']).read_text())
    uvicorn.run('server:create_app', factory=True, host='127.0.0.1', port=cfg['port'], workers=cfg['processes'], log_level='error', timeout_graceful_shutdown=8)
