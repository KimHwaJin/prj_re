"""Real PostgreSQL LISTEN/NOTIFY, shared reads, replay and bounded resources."""
import asyncio
from contextlib import AsyncExitStack
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select, update

from service_settings import get_settings
from api_service.core.enums import AgentRunStatus, DeleteYN
from api_service.models.common.agent_run_model import AgentRunModel
from api_service.models.common.session_model import SessionModel
from api_service.models.common.user_model import UserModel
from api_service.services.run_stream_service import RunStreamHub
from api_service.services.task_event_service import TaskEventService
from api_service.test.test_user_identity_postgres import database_url, harness, headers
from api_service.test.test_run_cleanup_postgres import runtime, enqueue


async def setup(h, **options):
    run = await enqueue(h)
    async with h.factory() as db:
        uid = await db.scalar(select(SessionModel.user_id).where(SessionModel.session_id==UUID(h.session_id)))
    cfg = get_settings().api.model_copy(update=options)
    return RunStreamHub(cfg, session_factory=h.factory), (uid, UUID(h.session_id), UUID(run['id']))


async def changed(entry, generation):
    async with asyncio.timeout(3):
        while entry.generation==generation:
            await entry.changed.wait()


@pytest.mark.asyncio
async def test_commit_wakes_both_listeners_but_uncommitted_and_rollback_do_not(runtime):
    hub,key=await setup(runtime)
    other=RunStreamHub(hub.settings,session_factory=runtime.factory)
    async with hub.subscribe(*key) as a, other.subscribe(*key) as b:
        await asyncio.wait_for(asyncio.gather(hub.ready.wait(),other.ready.wait()),3)
        ga,gb=a.generation,b.generation
        async with runtime.factory() as db:
            await db.execute(update(AgentRunModel).where(AgentRunModel.run_id==key[2]).values(status=AgentRunStatus.RUNNING))
            await asyncio.sleep(.1)
            assert (a.generation,b.generation)==(ga,gb)
            await db.rollback()
        await asyncio.sleep(.1)
        assert (a.generation,b.generation)==(ga,gb)
        async with runtime.factory() as db:
            await db.execute(update(AgentRunModel).where(AgentRunModel.run_id==key[2]).values(status=AgentRunStatus.RUNNING))
            await db.commit()
        await asyncio.gather(changed(a,ga),changed(b,gb))
        assert json.loads((await hub.read(a,0))[0].state)['status']=='running'
        assert hub.connection is not other.connection
    assert hub.connection is None and other.connection is None


@pytest.mark.asyncio
async def test_same_run_tabs_share_three_queries_and_idle_waits_do_not_query(runtime):
    hub,key=await setup(runtime,sse_reconcile_interval_seconds=10)
    sql=[]
    def capture(_c,_cur,stmt,*args):
        if stmt.lstrip().upper().startswith('SELECT'):sql.append(stmt)
    async with AsyncExitStack() as stack:
        entries=[await stack.enter_async_context(hub.subscribe(*key)) for _ in range(30)]
        await asyncio.wait_for(hub.ready.wait(),3)
        event.listen(runtime.engine.sync_engine,'before_cursor_execute',capture)
        try:
            frames=await asyncio.gather(*(hub.read(e,0) for e in entries))
            assert len(sql)==3 and len({id(frame) for frame,_ in frames})==1
            await asyncio.gather(*(hub.wait(e,g,.05) for e,(_,g) in zip(entries,frames)))
            assert len(sql)==3
            assert len(hub.entries)==1 and hub.subscribers==30
        finally:event.remove(runtime.engine.sync_engine,'before_cursor_execute',capture)
    assert not hub.entries and not hub.cache and hub.cached_bytes==0 and hub.listener is None


@pytest.mark.asyncio
async def test_event_only_commit_and_durable_cursor_replay(runtime):
    hub,key=await setup(runtime,sse_event_batch_size=2)
    async with hub.subscribe(*key) as entry:
        await asyncio.wait_for(hub.ready.wait(),3)
        frame,g=await hub.read(entry,0)
        cursor=frame.events[-1][0] if frame.events else 0
        async with runtime.factory() as db:
            for n in range(5):
                await TaskEventService.append_for_run(db,run_id=key[2],event_type='test.delta',payload={'n':n},commit=False)
            await db.commit()
        await changed(entry,g)
        seen=[]
        while True:
            frame,_=await hub.read(entry,cursor)
            if not frame.events:break
            seen.extend(seq for seq,_ in frame.events)
            cursor=seen[-1]
        assert len(seen)==5 and seen==sorted(set(seen))
        replay,_=await hub.read(entry,seen[-2])
        assert [seq for seq,_ in replay.events]==[seen[-1]]


@pytest.mark.asyncio
async def test_fallback_recovers_without_listener_and_heartbeat_has_no_sql(runtime):
    hub,key=await setup(runtime,sse_reconcile_interval_seconds=.15,sse_poll_interval_seconds=.02,sse_heartbeat_seconds=.03)
    async def unavailable(*args,**kwargs):raise OSError('simulated offline listener')
    hub.connect=unavailable
    async with hub.subscribe(*key) as entry:
        stream=hub.stream(SimpleNamespace(is_disconnected=AsyncMock(return_value=False)),entry,0)
        async with asyncio.timeout(3):
            while not (await anext(stream)).startswith('event: run.state'):pass
            async with runtime.factory() as db:
                await db.execute(update(AgentRunModel).where(AgentRunModel.run_id==key[2]).values(status=AgentRunStatus.RUNNING))
                await db.commit()
            chunks=[]
            while True:
                chunk=await anext(stream);chunks.append(chunk)
                if chunk.startswith('event: run.state') and '"status":"running"' in chunk:break
            assert ': heartbeat\n\n' in chunks
        await stream.aclose()


@pytest.mark.asyncio
async def test_listener_reconnect_reconciles_committed_state(runtime):
    hub,key=await setup(runtime)
    async with hub.subscribe(*key) as entry:
        await asyncio.wait_for(hub.ready.wait(),3)
        _,g=await hub.read(entry,0)
        hub.connection.terminate()
        async with runtime.factory() as db:
            await db.execute(update(AgentRunModel).where(AgentRunModel.run_id==key[2]).values(status=AgentRunStatus.RUNNING))
            await db.commit()
        await changed(entry,g)
        assert json.loads((await hub.read(entry,0))[0].state)['status']=='running'
    assert hub.listener is None


@pytest.mark.asyncio
async def test_deactivated_user_invalidates_cached_authorization(runtime):
    from fastapi import HTTPException
    hub,key=await setup(runtime)
    async with hub.subscribe(*key) as entry:
        await asyncio.wait_for(hub.ready.wait(),3)
        _,g=await hub.read(entry,0)
        async with runtime.factory() as db:
            await db.execute(update(UserModel).where(UserModel.user_id==key[0]).values(delete_yn=DeleteYN.Y));await db.commit()
        await changed(entry,g)
        with pytest.raises(HTTPException) as error:await hub.read(entry,0)
        assert error.value.status_code==404


@pytest.mark.asyncio
async def test_capacity_cache_bounds_and_shutdown_release(runtime):
    from fastapi import HTTPException
    hub,key=await setup(runtime,sse_max_connections=1)
    hub.CACHE_PAGES=2
    async with hub.subscribe(*key) as entry:
        with pytest.raises(HTTPException) as error:
            async with hub.subscribe(*key):pass
        assert error.value.status_code==503
        await asyncio.wait_for(hub.ready.wait(),3)
        for cursor in range(5):await hub.read(entry,cursor)
        assert len(hub.cache)<=2 and hub.cached_bytes<=hub.CACHE_BYTES
        hub.CACHE_BYTES=1
        hub.invalidate('*')
        await hub.read(entry,0)
        assert not hub.cache
        g=entry.generation
        await hub.close()
        await asyncio.wait_for(hub.wait(entry,g,10),.2)
    assert hub.subscribers==0 and not hub.entries and hub.listener is None


@pytest.mark.asyncio
async def test_notification_during_read_is_not_lost(runtime,monkeypatch):
    from api_service.services.public_run_service import PublicRunService
    hub,key=await setup(runtime)
    original=PublicRunService.snapshots
    async with hub.subscribe(*key) as entry:
        await asyncio.wait_for(hub.ready.wait(),3)
        async def racing(db,ids):
            value=await original(db,ids)
            hub.invalidate(str(key[2]))
            return value
        monkeypatch.setattr(PublicRunService,'snapshots',racing)
        _,g=await hub.read(entry,0)
        assert g!=entry.generation and not hub.cache
        await asyncio.wait_for(hub.wait(entry,g,5),.1)


@pytest.mark.asyncio
async def test_http_capacity_rejection_is_503_before_stream_headers(runtime):
    _,key=await setup(runtime)
    hub=runtime.app.state.run_stream_hub
    hub.settings=hub.settings.model_copy(update={'sse_max_connections':1})
    async with hub.subscribe(*key):
        response=await runtime.client.get(f'/api/v1/sessions/{key[1]}/runs/{key[2]}/stream',headers=headers(runtime.user['user_id']))
        assert response.status_code==503
        assert response.headers['retry-after']=='5'
        assert response.json()['detail']=='Run stream capacity exceeded.'
    assert hub.subscribers==0 and hub.listener is None


@pytest.mark.asyncio
async def test_other_process_commit_wakes_stream(runtime):
    import sys
    from sqlalchemy.engine import make_url
    hub,key=await setup(runtime)
    async with hub.subscribe(*key) as entry:
        await asyncio.wait_for(hub.ready.wait(),3)
        _,g=await hub.read(entry,0)
        dsn=make_url(hub.settings.database_url).set(drivername='postgresql').render_as_string(hide_password=False)
        code="import psycopg,sys; c=psycopg.connect(sys.argv[1]); c.execute(\"UPDATE agent_runs SET status='running' WHERE run_id=%s\",(sys.argv[2],)); c.commit(); c.close()"
        child=await asyncio.create_subprocess_exec(sys.executable,'-c',code,dsn,str(key[2]),stderr=asyncio.subprocess.PIPE)
        _,error=await child.communicate()
        assert child.returncode==0,error.decode()
        await changed(entry,g)
        assert json.loads((await hub.read(entry,0))[0].state)['status']=='running'


@pytest.mark.asyncio
async def test_burst_notifications_coalesce_and_isolate_other_runs(runtime):
    hub,key=await setup(runtime,sse_poll_interval_seconds=.05)
    async with hub.subscribe(*key) as entry:
        await asyncio.wait_for(hub.ready.wait(),3)
        await hub.read(entry,0)
        g=entry.generation
        hub.invalidate(str(uuid4()))
        assert entry.generation==g
        for _ in range(100):hub.invalidate(str(key[2]))
        frames=await asyncio.gather(*(hub.read(entry,0) for _ in range(20)))
        assert all(generation==g+100 for _,generation in frames)
        assert len({id(frame) for frame,_ in frames})==1

from api_service.test.test_short_transactions_postgres import small_pool


@pytest.mark.asyncio
async def test_real_http_auth_and_idle_stream_release_single_connection(small_pool):
    import socket
    import httpx
    import uvicorn
    h=small_pool
    run=await enqueue(h)
    hub=h.app.state.run_stream_hub
    hub.session_factory=h.factory
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen();sock.setblocking(False)
    from service_bootstrap import build_server
    server=build_server(h.app,get_settings())
    server.config.lifespan='off'
    server.config.log_level='error'
    from contextlib import nullcontext
    server.capture_signals=nullcontext  # Exercise the hook without re-raising SIGTERM into pytest.
    serving=asyncio.create_task(server.serve(sockets=[sock]))
    try:
        while not server.started:await asyncio.sleep(.01)
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{sock.getsockname()[1]}',timeout=5) as client:
            url=f'/api/v1/sessions/{h.session_id}/runs/{run["id"]}/stream'
            async with client.stream('GET',url,headers=headers(h.user['user_id'])) as response:
                assert response.status_code==200
                lines=response.aiter_lines()
                kind=None
                async for line in lines:
                    if line.startswith('event: '):kind=line[7:]
                    if line.startswith('data: ') and kind=='run.state':break
                await asyncio.wait_for(hub.ready.wait(),3)
                other=await client.get(f'/api/v1/sessions/{h.session_id}',headers=headers(h.user['user_id']))
                assert other.status_code==200
                async with asyncio.timeout(2):
                    while h.engine.pool.checkedout():await asyncio.sleep(.01)
                assert h.engine.pool.checkedout()==0
                import signal
                server.handle_exit(signal.SIGTERM,None)
                assert hub.closed
                async with asyncio.timeout(3):
                    async for _ in lines:pass
        async with asyncio.timeout(3):
            while hub.subscribers:await asyncio.sleep(.01)
        assert h.engine.pool.checkedout()==0 and hub.listener is None
    finally:
        server.should_exit=True
        await asyncio.wait_for(serving,5)
        await hub.close()
        sock.close()
