"""Real loopback HTTP SSE A/B against explicit source checkout and scratch DB.

PYTHONPATH=<source>/src DTEST_IDENTITY_TEST_DATABASE_URL=<local identity_test>
DTEST_SSE_BENCH_REPORT=<output.json> python -m pytest <this-file> -q -s
No LLM, Executor or Redis calls. Uses the guarded existing migration fixture.
"""
import asyncio
import json
import os
from pathlib import Path
import socket
import time
from uuid import UUID

import httpx
import pytest
from sqlalchemy import event, update, text
import uvicorn

from tests.api_service.test_user_identity_postgres import database_url, harness, headers, add_session
from tests.api_service.test_run_cleanup_postgres import runtime, enqueue
from api_service.models.agent_run_model import AgentRunModel
from api_service.models.enums import AgentRunStatus
from api_service.api.v1.routes import runs as routes


@pytest.mark.asyncio
async def test_sse_comparison(runtime,monkeypatch):
    h=runtime
    monkeypatch.setattr(routes,'get_session_factory',lambda:h.factory)
    if hasattr(h.app.state,'run_stream_hub'):
        h.app.state.run_stream_hub.session_factory=h.factory
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen();sock.setblocking(False)
    url=f'http://127.0.0.1:{sock.getsockname()[1]}'
    server=uvicorn.Server(uvicorn.Config(h.app,log_level='error',lifespan='off'))
    serving=asyncio.create_task(server.serve(sockets=[sock]))
    while not server.started:await asyncio.sleep(.01)
    results=[];measuring=False;selects=0
    def trace(_c,_cur,stmt,*args):
        nonlocal selects
        if measuring and stmt.lstrip().upper().startswith('SELECT'):selects+=1
    event.listen(h.engine.sync_engine,'before_cursor_execute',trace)
    try:
        async with httpx.AsyncClient(base_url=url,timeout=15,limits=httpx.Limits(max_connections=100)) as client:
            for scenario in ('distinct_runs','same_run_tabs'):
                for count in (1,10,30,50):
                    targets=[]
                    for _ in range(count if scenario=='distinct_runs' else 1):
                        sid=await add_session(h,h.user);run=await enqueue(h,sid)
                        targets.append((sid,run.get('run_id', run.get('id'))))
                    if scenario=='same_run_tabs':targets*=count
                    ready=[asyncio.Event() for _ in targets];delivered=[asyncio.Event() for _ in targets]
                    received=[None]*count
                    async def watch(i,sid,rid):
                        async with client.stream('GET',f'/api/v1/sessions/{sid}/runs/{rid}/stream',headers=headers(h.user['user_id'])) as response:
                            assert response.status_code==200
                            kind=None
                            async for line in response.aiter_lines():
                                if line.startswith('event: '):kind=line[7:]
                                elif line.startswith('data: ') and kind=='run.state':
                                    state=json.loads(line[6:]);ready[i].set()
                                    if state['status']=='running':
                                        received[i]=time.perf_counter();delivered[i].set()
                    streams=[asyncio.create_task(watch(i,*target)) for i,target in enumerate(targets)]
                    for i,task in enumerate(streams):
                        task.add_done_callback(lambda _task, index=i: ready[index].set())
                    try:
                        await asyncio.wait_for(asyncio.gather(*(x.wait() for x in ready)),15)
                        for task in streams:
                            if task.done():
                                task.result()
                                raise AssertionError("SSE ended before the idle measurement")
                        hub=getattr(h.app.state,'run_stream_hub',None)
                        if hub:await asyncio.wait_for(hub.ready.wait(),3)
                        await asyncio.sleep(.6) # Exclude connection/auth/initial catch-up.
                        selects=0;measuring=True;started=time.perf_counter()
                        await asyncio.sleep(3)
                        elapsed=time.perf_counter()-started;measuring=False
                        idle=selects
                        async with h.factory() as db:
                            idle_transactions=await db.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND state='idle in transaction'"))
                        if hub:
                            assert idle_transactions==0
                        async with h.factory() as db:
                            await db.execute(update(AgentRunModel).where(AgentRunModel.run_id.in_({UUID(rid) for _,rid in targets})).values(status=AgentRunStatus.RUNNING))
                            committed=time.perf_counter()
                            await db.commit()
                        await asyncio.wait_for(asyncio.gather(*(x.wait() for x in delivered)),15)
                        latency=sorted((x-committed)*1000 for x in received)
                        results.append({'scenario':scenario,'connections':count,'distinct_runs':len(set(targets)),
                            'idle_transactions':idle_transactions,'idle_seconds':elapsed,'idle_selects':idle,'idle_selects_per_second':idle/elapsed,
                            'change_delivery_ms_mean':sum(latency)/len(latency),
                            'change_delivery_ms_p95':latency[min(len(latency)-1,int(.95*len(latency)))],
                            'listener_connections':int(bool(hub and hub.connection)),
                            'shared_subscriptions':len(hub.entries) if hub else None})
                    finally:
                        measuring=False
                        for task in streams:task.cancel()
                        await asyncio.gather(*streams,return_exceptions=True)
                    print(json.dumps(results[-1]),flush=True)
                    if hub:
                        async with asyncio.timeout(5):
                            while hub.subscribers:await asyncio.sleep(.01)
                        assert not hub.entries and not hub.cache and hub.listener is None
    finally:
        event.remove(h.engine.sync_engine,'before_cursor_execute',trace)
        server.should_exit=True
        await asyncio.wait_for(serving,10)
        sock.close()
    report={'source':str(Path(__import__('api_service').__file__).resolve().parents[2]),
        'method':'real loopback HTTP SSE, local PostgreSQL, LLM/Executor disabled; 3-second idle windows after warmup; SELECT count excludes handshake and commits; one trial per cell',
        'results':results}
    Path(os.environ['DTEST_SSE_BENCH_REPORT']).write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))
