"""Actual HTTP delivery and durable Run/Task/session recovery protections."""
import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

import api_service.agent_run_worker as worker
import api_service.services.run_service as runs
from service_contracts.execution import ExecutionNeedsRecovery
from api_service.core.execution_lifecycle import execution_health
from api_service.core.enums import AgentRunStatus, TaskStatus
from integrations.executor import client as api
from api_service.test.test_user_identity_postgres import database_url, harness, headers
from api_service.test.test_run_cleanup_postgres import runtime, enqueue, rows, wait_until_recovery
from api_service.test.test_public_run_postgres import path, state
from api_service.test.test_executor_async_http import local_server, settings, receipt


@pytest.mark.asyncio
@pytest.mark.parametrize("failure",["lost_response","cancel_api","shutdown","after_response_failure"])
async def test_possible_submission_blocks_retry_and_keeps_session_locked(runtime,monkeypatch,failure):
    h=runtime
    received=asyncio.Event()
    async def handle(request):
        received.set()
        if failure=="lost_response": return None
        if failure in {"cancel_api","shutdown"}: await asyncio.Event().wait()
        return 202,receipt()
    async with local_server(handle) as server:
        cfg=settings(server.url)
        async with api.ExecutorClient(cfg) as client:
            async def graph(**kwargs):
                await api.submit_execution_start(cfg,{"idempotency_key":str(kwargs["run_id"])+":start"},client=client)
                raise ValueError("local persistence failed after acceptance")
            monkeypatch.setattr(runs,"ainvoke_user_turn",graph)
            first=await enqueue(h)
            running=asyncio.create_task(worker.execute_claimed(await worker.claim_one()))
            await asyncio.wait_for(received.wait(),3)
            if failure=="cancel_api":
                requested=await h.client.post(path(h,first["id"])+"/cancel",headers=headers(h.user["user_id"]),json={"reason":"stop"})
                assert requested.status_code==202,requested.text
            elif failure=="shutdown": running.cancel()
            with pytest.raises((ExecutionNeedsRecovery,asyncio.CancelledError)):
                await asyncio.wait_for(running,3)
            run,task=await wait_until_recovery(h,first["id"])
            assert task.recovery_required and task.lock_token is not None
            assert run.status not in {AgentRunStatus.SUCCESS,AgentRunStatus.CANCELED}
            assert run.failure["retry_scheduled"] is False
            current=await state(h,first["id"])
            assert current["status"]=="recovery_required"
            blocked=await h.client.post(path(h),headers={**headers(h.user["user_id"]),"Idempotency-Key":str(uuid4())},
                json={"input":{"messages":[{"role":"user","content":"new"}]}})
            assert blocked.status_code==409,blocked.text
            assert len(server.requests)==1 and run.attempt_count==1
            with pytest.raises(ExecutionNeedsRecovery):
                await worker.claim_one()


@pytest.mark.asyncio
async def test_known_http_rejection_is_terminal_without_recovery_or_retries(runtime,monkeypatch):
    h=runtime
    async def handle(request): return 422,{"message":"invalid input"}
    async with local_server(handle) as server:
        cfg=settings(server.url)
        async with api.ExecutorClient(cfg) as client:
            async def graph(**kwargs):
                await api.submit_execution_start(cfg,{"idempotency_key":"rejected"},client=client)
            monkeypatch.setattr(runs,"ainvoke_user_turn",graph)
            first=await enqueue(h)
            await worker.execute_claimed(await worker.claim_one())
            run,task=await rows(h,first["id"])
            assert run.status==AgentRunStatus.ERROR and task.status==TaskStatus.ERROR
            assert not task.recovery_required and task.lock_token is None
            assert run.attempt_count==1 and len(server.requests)==1
            assert execution_health.healthy


@pytest.mark.asyncio
async def test_accepted_submission_yields_executor_wait_without_holding_owner(runtime,monkeypatch):
    h=runtime
    execution=str(uuid4()); graph_task=str(uuid4())
    async def handle(request): return 202,{**receipt(),"execution_id":execution}
    async with local_server(handle) as server:
        cfg=settings(server.url)
        async with api.ExecutorClient(cfg) as client:
            async def graph(**kwargs):
                result=await api.submit_execution_start(cfg,{"idempotency_key":"accepted"},client=client)
                return {"routing_result":{"route":"analysis"},"task_id":graph_task,
                    "execution_id":result["body"]["execution_id"],
                    "__interrupt__":[SimpleNamespace(value={"kind":"EXECUTOR_EVENT"})]}
            monkeypatch.setattr(runs,"ainvoke_user_turn",graph)
            first=await enqueue(h)
            await worker.execute_claimed(await worker.claim_one())
            run,task=await rows(h,first["id"])
            assert run.status==AgentRunStatus.INTERRUPTED and task.status==TaskStatus.WAITING_INPUT
            assert task.lock_token is None and not task.recovery_required
            assert (await state(h,first["id"]))["status"]=="waiting_executor"
            assert len(server.requests)==1 and server.active==0
            assert await worker.claim_one() is None


@pytest.mark.asyncio
async def test_executor_event_post_ambiguity_keeps_session_owner_for_recovery(runtime,monkeypatch):
    from sqlalchemy import select
    from api_service.services.session_execution import run_event_owned
    from api_service.models.common.session_execution_model import SessionExecutionModel
    from api_service.agent_worker.langgraph_adapter import LangGraphEventAdapter
    from api_service.worker import EventContext, ExecutorEvent, DeferEvent
    from unittest.mock import AsyncMock
    from uuid import UUID
    h=runtime
    execution=uuid4(); graph_task=uuid4()
    monkeypatch.setattr(runs,"ainvoke_user_turn",AsyncMock(return_value={
        "routing_result":{"route":"analysis"},"task_id":str(graph_task),"execution_id":str(execution),
        "__interrupt__":[SimpleNamespace(value={"kind":"EXECUTOR_EVENT"})]}))
    first=await enqueue(h)
    await worker.execute_claimed(await worker.claim_one())
    context=EventContext("test",h.session_id,str(graph_task),execution,uuid4(),
        ExecutorEvent(event_id=uuid4(),execution_id=execution,event_type="execution.completed",event_sequence=1,
            schema_version="1.0",occurred_at="2026-09-29T00:00:00+00:00",payload={}))
    async def handle(request): return None
    async with local_server(handle) as server:
        cfg=settings(server.url)
        async with api.ExecutorClient(cfg) as client:
            async def invoke(*args,**kwargs):
                await api.submit_execution_artifact(cfg,str(execution),{"idempotency_key":"report"},client=client)
            adapter=LangGraphEventAdapter(SimpleNamespace(ainvoke=invoke))
            async def operation(): return await adapter._invoke(None,{},values={},durability="sync")
            with pytest.raises(ExecutionNeedsRecovery):
                await run_event_owned(context,operation)
            async with h.factory() as db:
                owner=await db.scalar(select(SessionExecutionModel).where(SessionExecutionModel.session_id==UUID(h.session_id)))
                assert owner.recovery_required and owner.token is not None
            with pytest.raises(DeferEvent):
                await run_event_owned(context,operation)
            assert len(server.requests)==1
