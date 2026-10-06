"""Real Inbox/command transactions; HTTP history controlled at the transport edge."""
import asyncio
from uuid import uuid4
import httpx
import pytest
from sqlalchemy import text

from api_service.workers.executor_events.ingress import EventRouter
from tests.api_service.test_agent_commands_postgres import commands, event, rows
from tests.api_service.test_user_identity_postgres import database_url, harness
from tests.api_service.test_run_cleanup_postgres import runtime

pytestmark = pytest.mark.asyncio


async def status(h, eid):
    async with h.store.pool.connection() as conn:
        return await (await conn.execute('SELECT last_sequence,last_error,next_scan_at>now() FROM ew_bindings WHERE execution_id=%s',(eid,))).fetchone()


async def ready(h, eid):
    async with h.store.pool.connection() as conn:
        await conn.execute('UPDATE ew_bindings SET next_scan_at=now() WHERE execution_id=%s',(eid,))


async def test_reversed_duplicate_delivery_history_repair_and_two_routers(commands):
    h=commands; eid=uuid4(); events=[event(eid,n) for n in (1,2,3)]
    await h.store.register(execution_id=eid,session_id=h.session_id,task_id=str(uuid4()))
    await h.store.ingest(events[2]); await h.store.ingest(events[0])
    requests=[]
    def history(request):
        requests.append(str(request.url))
        assert request.url.path==f'/api/v1/executions/{eid}/events'
        after=int(request.url.params['after_sequence'])
        return httpx.Response(200,json={'items':[e.model_dump(mode='json') for e in events if e.event_sequence>after], 'has_more':False,'next_cursor':None})
    async with httpx.AsyncClient(base_url='http://executor/api/v1/',transport=httpx.MockTransport(history)) as http:
        router=EventRouter(h.store,http,{'execution.completed'})
        await asyncio.gather(router.once(),router.once())
        for e in events:await h.store.ingest(e)
        assert await router.once()==0
    assert requests and [c.payload['event']['event_sequence'] for c in await rows(h)]==[1,2,3]
    assert (await status(h,eid))[:2]==(3,None)


@pytest.mark.parametrize('failure',['404','empty','mixed','invalid'])
async def test_history_failure_preserves_gap_then_late_delivery_resumes(commands,failure):
    h=commands; eid=uuid4(); first,later=event(eid,1),event(eid,2)
    await h.store.register(execution_id=eid,session_id=h.session_id,task_id=str(uuid4()))
    await h.store.ingest(later)
    def history(request):
        if failure=='404':return httpx.Response(404)
        data=[] if failure=='empty' else [event(uuid4()).model_dump(mode='json')] if failure=='mixed' else [{}]
        return httpx.Response(200,json={'items':data,'has_more':False})
    async with httpx.AsyncClient(base_url='http://executor/api/v1/',transport=httpx.MockTransport(history)) as http:
        router=EventRouter(h.store,http,{'execution.completed'})
        assert await router.once()==0
        sequence,error,deferred=await status(h,eid)
        assert sequence==0 and error and deferred and not await rows(h)
        await h.store.ingest(first); await ready(h,eid)
        assert await router.once()==2
    assert [c.payload['event']['event_sequence'] for c in await rows(h)]==[1,2]


async def test_paginated_reclaim_catchup_does_not_skip_tail(commands):
    h=commands; eid=uuid4(); events=[event(eid,n) for n in range(1,6)]
    await h.store.register(execution_id=eid,session_id=h.session_id,task_id=str(uuid4()))
    await h.store.ingest(events[0],catch_up=True)
    offsets=[]
    def history(request):
        after=int(request.url.params['after_sequence']);offsets.append(after)
        page=[e for e in events if e.event_sequence>after][:2]
        more=page[-1].event_sequence<5 if page else False
        return httpx.Response(200,json={'items':[e.model_dump(mode='json') for e in page], 'has_more':more,'next_cursor':'opaque' if more else None})
    async with httpx.AsyncClient(base_url='http://executor/api/v1/',transport=httpx.MockTransport(history)) as http:
        router=EventRouter(h.store,http,{'execution.completed'},batch_size=2)
        for _ in range(5):await router.once()
    assert [c.payload['event']['event_sequence'] for c in await rows(h)]==[1,2,3,4,5]
    assert offsets==[0,2,4]
    assert not await h.store.scan_candidates(10)


async def test_conflicting_sequence_is_rejected_without_overwriting_inbox(commands):
    h=commands;eid=uuid4();original=event(eid,1)
    await h.store.register(execution_id=eid,session_id=h.session_id,task_id=str(uuid4()))
    await h.store.ingest(original)
    with pytest.raises(ValueError,match='Conflicting'):
        await h.store.ingest(event(eid,1))
    assert await h.store.advance(eid,{'execution.completed'},10)==(1,None)
    assert [c.payload['event']['event_id'] for c in await rows(h)]==[str(original.event_id)]


async def test_default_root_history_route_with_real_http_socket_and_inbox(commands):
    from tests.api_service.test_executor_async_http import local_server
    from api_service.workers.executor_events.runtime import ExecutorWorker
    from service_settings import load_settings
    h=commands;eid=uuid4();events=[event(eid,n) for n in (1,2,3)]
    await h.store.register(execution_id=eid,session_id=h.session_id,task_id=str(uuid4()))
    await h.store.ingest(events[2]);await h.store.ingest(events[0])
    async def history(request):
        assert request['path'].startswith(f'/api/v1/executions/{eid}/events?')
        assert 'after_sequence=1' in request['path']
        return 200,{'items':[e.model_dump(mode='json') for e in events[1:]],'has_more':False,'next_cursor':None}
    async with local_server(history) as server:
        settings=load_settings(config={'EXECUTOR_BASE_URL':server.url},environ={})
        worker=ExecutorWorker(settings.worker,{'execution.completed'})
        worker.router.store=h.store
        try:
            assert await worker.router.once()==3
            assert len(server.requests)==1
        finally:
            await worker.http.aclose();await worker.redis.aclose();await worker.pool.close()
    assert [c.payload['event']['event_sequence'] for c in await rows(h)]==[1,2,3]
    assert (await status(h,eid))[:2]==(3,None)
