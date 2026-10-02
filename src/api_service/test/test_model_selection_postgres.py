"""HTTP admission, queue retries, persistent HITL and Executor model continuity."""
from agent_service.runtime.initial_request import record_initial_request
from agent_service.runtime.user_resume import record_user_resume, user_interrupt

from contextlib import asynccontextmanager
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt
from sqlalchemy import select, func

import service_settings
import api_service.agent_run_worker as worker
import api_service.services.run_service as runs
import api_service.services.agent_graph_service as graphs
import api_service.services.executor_completion as completion
from service_runtime.model_selection import build_catalog, validate_checkpoint_selection
import agent_service.agents.analysis.planning.runtime as runtime_module
from agent_service.agents.analysis.planning.runtime import PlanningRuntime
from agent_service.context import AgentContext
from agent_service.agents.analysis.tests.test_agent_middleware import response
from agent_service.runtime.langgraph.checkpointer import create_checkpointer
from api_service.agent_worker.langgraph_adapter import LangGraphEventAdapter
from api_service.models.common.agent_run_model import AgentRunModel as Run
from api_service.models.common.task_model import TaskModel as Task
from api_service.services.session_execution import run_event_owned
from api_service.worker import EventContext, ExecutorEvent
from api_service.test.test_user_identity_postgres import database_url, harness, headers, add_session
from api_service.test.test_run_cleanup_postgres import runtime, rows
from api_service.test.test_public_run_postgres import path, state, resume, execute, waiting


ENTRIES={"alpha":{"provider":"mock","model_name":"model-a"},
         "beta":{"provider":"mock","model_name":"model-b"}}


def use_catalog(monkeypatch,default="alpha",entries=None):
    old=service_settings.get_settings()
    catalog=build_catalog(old.agent,ENTRIES if entries is None else entries,default)
    monkeypatch.setattr(service_settings,"_snapshot",replace(old,agent=replace(old.agent,model_catalog=catalog)))
    return catalog


async def start(h,*,name=None,key=None,extra=None,sid=None):
    body={"input":{'content': [{'type': 'text', 'text': "hello"}]}}
    if name is not None: body["main_model_name"]=name
    if extra: body.update(extra)
    return await h.client.post(f"/api/v1/sessions/{sid or h.session_id}/runs",
        headers={**headers(h.user["user_id"]),"Idempotency-Key":key or str(uuid4())},json=body)


@pytest.mark.asyncio
async def test_default_explicit_replay_and_rejected_models_leave_no_rows(runtime,monkeypatch):
    h=runtime
    catalog=use_catalog(monkeypatch)
    for extra in ({"main_model_name":"unknown"},{"main_model_name":""},
                  {"metadata":{"_model_selection":catalog.select().model_dump()}}):
        bad=await start(h,extra=extra)
        assert bad.status_code==422,bad.text
    async with h.factory() as db:
        assert await db.scalar(select(func.count()).select_from(Run))==0
        assert await db.scalar(select(func.count()).select_from(Task))==0
    first=await start(h,key="default-request")
    assert first.status_code==202,first.text
    assert first.json()["main_model_name"]=="alpha"
    use_catalog(monkeypatch,"beta")
    replay=await start(h,key="default-request")
    assert replay.status_code==202 and replay.json()==first.json()
    assert (await start(h,key="default-request",name="beta")).status_code==409
    second=await start(h,sid=await add_session(h,h.user))
    assert second.json()["main_model_name"]=="beta"
    explicit=await start(h,name="alpha",sid=await add_session(h,h.user))
    assert explicit.json()["main_model_name"]=="alpha"
    async with h.factory() as db:
        root=await db.get(Run,UUID(first.json()["run_id"]))
        pin=root.metadata_json["_model_selection"]
        assert pin==catalog.select().model_dump()
        assert set(pin)=={"name","revision"}


@pytest.mark.asyncio
async def test_retry_and_resume_keep_initial_choice_after_default_changes(runtime,monkeypatch):
    h=runtime
    catalog=use_catalog(monkeypatch)
    initial=AsyncMock(side_effect=[ValueError("temporary test fault"),waiting()])
    resumed=AsyncMock(return_value=waiting())
    monkeypatch.setattr(runs,"ainvoke_user_turn",initial)
    monkeypatch.setattr(runs,"ainvoke_resume",resumed)
    first=(await start(h,name="beta")).json()
    await execute()
    use_catalog(monkeypatch,"beta")
    await execute()
    current=await state(h,first["run_id"])
    assert current["status"]=="waiting_input"
    use_catalog(monkeypatch,"alpha")
    accepted=await resume(h,current)
    assert accepted.status_code==202,accepted.text
    await execute()
    pin=catalog.select("beta").model_dump()
    assert all(c.kwargs["model_selection"]==pin for c in initial.await_args_list+resumed.await_args_list)
    assert (await state(h,first["run_id"]))["main_model_name"]=="beta"
    async with h.factory() as db:
        records=list(await db.scalars(select(Run)))
        assert len(records)==2 and all(r.metadata_json["_model_selection"]==pin for r in records)


@pytest.mark.asyncio
@pytest.mark.parametrize("change",["removed","changed","legacy"])
async def test_unavailable_resume_is_conflict_without_queue_side_effects(runtime,monkeypatch,change):
    h=runtime
    use_catalog(monkeypatch)
    monkeypatch.setattr(runs,"ainvoke_user_turn",AsyncMock(return_value=waiting()))
    first=(await start(h,key="original")).json()
    await execute()
    current=await state(h,first["run_id"])
    if change=="removed":
        use_catalog(monkeypatch,"beta",{"beta":ENTRIES["beta"]})
    elif change=="changed":
        use_catalog(monkeypatch,entries={**ENTRIES,"alpha":{"provider":"mock","model_name":"new-model"}})
    else:
        async with h.factory() as db:
            root=await db.get(Run,UUID(first["run_id"]))
            root.metadata_json={k:v for k,v in root.metadata_json.items() if k!="_model_selection"}
            await db.commit()
    failed=await resume(h,current)
    assert failed.status_code==409,failed.text
    assert (await state(h,first["run_id"]))["status"]=="waiting_input"
    assert (await start(h,key="original")).status_code==202
    async with h.factory() as db:
        assert await db.scalar(select(func.count()).select_from(Run))==1
    canceled=await h.client.post(path(h,first["run_id"])+"/cancel",headers=headers(h.user["user_id"]),json={})
    assert canceled.status_code==202 and canceled.json()["status"]=="canceled"


@pytest.mark.asyncio
async def test_model_removed_after_admission_fails_once_before_graph(runtime,monkeypatch):
    h=runtime
    use_catalog(monkeypatch)
    invoked=AsyncMock()
    monkeypatch.setattr(runs,"ainvoke_user_turn",invoked)
    first=(await start(h)).json()
    use_catalog(monkeypatch,"beta",{"beta":ENTRIES["beta"]})
    await execute()
    invoked.assert_not_called()
    current=await state(h,first["run_id"])
    assert current["status"]=="error" and current["failure"]["code"]=="RUN_MODEL_UNAVAILABLE"
    assert current["attempt_count"]==1
    assert await worker.claim_one() is None


@pytest.mark.asyncio
async def test_actual_roles_keep_model_through_postgres_restart_hitl_and_executor(runtime,monkeypatch):
    h=runtime
    specs={alias:{"model_name":model,"api_base_url":"http://llm.invalid/v1","api_key":"private-key"}
           for alias,model in [("alpha","model-a"),("beta","model-b")]}
    catalog=use_catalog(monkeypatch,"beta",specs)
    calls=[]
    async def transport(request):
        body=json.loads(request.content); calls.append(body)
        value=json.loads(body['messages'][-1]['content'])
        if 'observations' in value:
            answer={'markdown':'# Interpretation','evidence_steps':[]}
        else:
            answer={'kind':'answer','message':'Answer by '+body['model'],'plans':[],'grounding':{'scope':'general'}}
        return response(json.dumps(answer))
    graph_task,execution,command,event_id=[uuid4() for _ in range(4)]
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as ac:
        with httpx.Client(transport=httpx.MockTransport(lambda _: (_ for _ in ()).throw(AssertionError("sync HTTP")))) as sc:
            def model(settings):
                return ChatOpenAI(model=settings.model_name,api_key="test",base_url=settings.api_base_url,
                    max_retries=0,http_async_client=ac,http_client=sc)
            monkeypatch.setattr(runtime_module,"create_chat_model",model)
            def build(saver):
                runtime=PlanningRuntime(service_settings.get_settings().agent)
                def context(s):
                    return AgentContext(**{k:s[k] for k in ('user_id','project_id','session_id','model_selection')},
                        project_system_prompt=s.get('project_system_prompt',''),project_prompt_version=s.get('project_prompt_version'))
                @record_initial_request
                async def first(s):
                    await runtime.respond({**s,"user_request":"first"},context(s),[])
                    return {**s,"routing_result":{"route":"analysis"},"task_id":str(graph_task),"execution_id":str(execution),
                            "ew_pending":{"command_id":str(command)}}
                @record_user_resume
                async def approval(s):
                    user_interrupt({"kind":"USER_APPROVAL"})
                    await runtime.respond({**s,"user_request":"after approval"},context(s),[])
                    return s
                async def external(s):
                    interrupt({"kind":"EXECUTOR_EVENT","task_id":str(graph_task),"execution_id":str(execution)})
                    report=await runtime.execution_role("report",s,{"observations":[]})
                    return {**s,"execution_status":"SUCCEEDED","ew_receipts":{str(command):str(event_id)},
                            "final_response":report.model_dump()}
                builder=StateGraph(dict)
                for name,node in [("first",first),("approval",approval),("external",external)]: builder.add_node(name,node)
                for a,b in [(START,"first"),("first","approval"),("approval","external"),("external",END)]: builder.add_edge(a,b)
                return builder.compile(checkpointer=saver)
            @asynccontextmanager
            async def graph_context():
                async with create_checkpointer(database_url=service_settings.get_settings().agent.checkpoint_db_uri,
                    setup_on_start=True,min_size=1,max_size=2,timeout=2) as saver:
                    yield build(saver)
            def fresh_runtime():
                rt=graphs.AgentGraphRuntime()
                monkeypatch.setattr(rt,"_graph_context",graph_context)
                monkeypatch.setattr(graphs,"runtime",rt)
                return rt
            first=(await start(h)).json()
            assert first["main_model_name"]=="beta"
            rt=fresh_runtime()
            try:
                await execute()
                current=await state(h,first["run_id"])
                assert current["status"]=="waiting_input",current
                await rt.shutdown()
                use_catalog(monkeypatch,"alpha",specs)
                rt=fresh_runtime()
                assert (await resume(h,current)).status_code==202
                await execute()
                current=await state(h,first["run_id"])
                assert current["status"]=="waiting_executor",current
                await rt.shutdown()
                rt=fresh_runtime()
                monkeypatch.setattr(completion,"get_session_factory",lambda:h.factory)
                context=EventContext("test",h.session_id,str(graph_task),execution,command,
                    ExecutorEvent(event_id=event_id,execution_id=execution,event_type="execution.completed",event_sequence=1,
                        schema_version="1.0",occurred_at="2026-09-29T00:00:00+00:00",payload={}))
                async with rt.open_graph() as graph:
                    before=await graph.aget_state(context.graph_config)
                    assert before.values["model_selection"]==catalog.select().model_dump()
                    assert "private-key" not in json.dumps(before.values,default=str)
                    adapter=LangGraphEventAdapter(graph,model_validator=validate_checkpoint_selection)
                    async def handle():
                        await adapter(context)
                        await completion.synchronize_executor_completion(context,graph)
                    await run_event_owned(context,handle)
                    # Delivery replay must not trigger a second report LLM call.
                    await run_event_owned(context,handle)
                done=await state(h,first["run_id"])
                assert done["status"]=="success",done
                assert done["main_model_name"]=="beta"
                assert len(calls)==3 and all(call["model"]=="model-b" for call in calls)
            finally:
                await rt.shutdown()
