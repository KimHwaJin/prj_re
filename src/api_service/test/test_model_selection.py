"""Model references, central config and concurrent real create_agent roles."""
from api_service.runs.requests import request_digest
from api_service.runs.policy import is_retryable
import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from langchain_openai import ChatOpenAI

import service_settings
from agent_service.context import AgentContext
from service_runtime.model_selection import ModelSelectionError, build_catalog, validate_checkpoint_selection
import agent_service.agents.analysis.planning.runtime as runtime_module
from agent_service.agents.analysis.planning.runtime import PlanningRuntime
from agent_service.agents.analysis.tests.test_agent_middleware import response, ROLES, role_payload
from api_service.schemas.common.run_schema import RunCreate, RunResume
from api_service.runs.graph_invocation import GraphInvocation


def configured(default="alpha", **extra):
    return service_settings.load_settings(config={"MODEL_PROVIDER":"mock", "DEFAULT_MODEL":default,
        "MODEL_CATALOG":{
            "alpha":{"provider":"mock", "model_name":"model-a"},
            "beta":{"provider":"mock", "model_name":"model-b"}}, **extra}, environ={})


def test_config_precedence_and_no_credential_in_pin_or_errors():
    spec={"model_name":"model-a", "api_base_url":"https://internal.example/v1", "api_key":"private-key"}
    snapshot=service_settings.load_settings(config={'MODEL_CATALOG': {'alpha': spec}, 'DEFAULT_MODEL': 'alpha'},
        environ={"DEFAULT_MODEL":"wrong", "MODEL_CATALOG":"invalid-secret"})
    catalog=snapshot.agent.model_catalog
    pin=catalog.select().model_dump()
    assert set(pin)=={"name","revision"} and pin["name"]=="alpha"
    assert all(v not in json.dumps(pin) for v in ("private-key","internal.example","model-a"))
    rotated=build_catalog(snapshot.agent,{"alpha":{**spec,"api_key":"rotated"}},"alpha")
    assert rotated.select()==catalog.select()
    assert "private-key" not in repr(catalog.models["alpha"])
    with pytest.raises(TypeError):
        catalog.models["x"]=catalog.models["alpha"]
    env=service_settings.load_settings(config={},environ={"DEFAULT_MODEL":"alpha","MODEL_CATALOG":json.dumps({"alpha":spec})})
    assert env.agent.model_catalog.select()==catalog.select()


@pytest.mark.parametrize("changes",[
    {"MODEL_CATALOG":None},{"MODEL_CATALOG":{}},{"MODEL_CATALOG":"bad-secret"},
    {"DEFAULT_MODEL":""},{"DEFAULT_MODEL":"missing"},{"DEFAULT_MODEL":None},
    {"MODEL_CATALOG":{"alpha":{"model_name":"x","api_key":"secret"}}},
    {"MODEL_CATALOG":{"alpha":{"provider":"mock","model_name":"x","unknown":"secret"}}},
    {"MODEL_CATALOG":{"alpha":{"provider":"mock","model_name":"x","timeout_seconds":0}}},
    {"MODEL_CATALOG":{"alpha":{"provider":"mock","model_name":"x","temperature":float("nan")}}},
])
def test_bad_catalog_never_falls_back(changes):
    with pytest.raises(service_settings.ConfigurationError) as err:
        configured(**changes)
    assert "secret" not in str(err.value)


def test_pins_survive_default_change_but_not_model_change(monkeypatch):
    old=configured()
    pin=old.agent.model_catalog.select().model_dump()
    new=configured("beta")
    monkeypatch.setattr(service_settings,"_snapshot",new)
    validate_checkpoint_selection({"model_selection":pin},pin)
    assert new.agent.model_catalog.select().name=="beta"
    assert new.agent.model_catalog.resolve(pin).model_name=="model-a"
    altered=configured(MODEL_CATALOG={"alpha":{"provider":"mock","model_name":"changed"}})
    with pytest.raises(ModelSelectionError,match="changed"):
        altered.agent.model_catalog.resolve(pin)
    with pytest.raises(ModelSelectionError,match="match"):
        validate_checkpoint_selection({"model_selection":pin},new.agent.model_catalog.select().model_dump())
    with pytest.raises(ModelSelectionError,match="recovery"):
        validate_checkpoint_selection({})
    assert not is_retryable(ModelSelectionError("changed"))


def test_legacy_idempotency_hash_preserved_and_explicit_choice_distinct():
    import hashlib
    body={"input":{"messages":[{"role":"user","content":"hello"}]}}
    request=RunCreate(**body)
    legacy=request.model_dump(mode="json",exclude={"main_model_name"})
    assert request_digest(request)==hashlib.sha256(json.dumps(legacy,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
    assert request_digest(request)!=request_digest(RunCreate(**body,main_model_name="default"))
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        RunResume(command={},resume_token="00000000-0000-0000-0000-000000000000",main_model_name="beta")




@pytest.mark.asyncio
@pytest.mark.parametrize("role,content",ROLES)
async def test_all_roles_route_concurrently_without_shared_model_mutation(monkeypatch,role,content):
    calls=[]
    entered=asyncio.Event()
    async def handle(request):
        calls.append(json.loads(request.content))
        if len(calls)==2: entered.set()
        await asyncio.wait_for(entered.wait(),2)
        return response(content)
    def no_sync(request): raise AssertionError("sync HTTP called")
    specs={name:{"model_name":model,"api_base_url":"http://llm.invalid/v1","api_key":"secret",
                  "structured_output_mode":mode}
           for name,model,mode in [("alpha","model-a","prompt_json"),("beta","model-b","provider_json_schema")]}
    snapshot=configured(MODEL_CATALOG=specs)
    constructions=[]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as ac:
        with httpx.Client(transport=httpx.MockTransport(no_sync)) as sc:
            def create(settings):
                constructions.append(settings.model_name)
                return ChatOpenAI(model=settings.model_name,api_key="test",base_url=settings.api_base_url,
                                  max_retries=0,http_async_client=ac,http_client=sc)
            monkeypatch.setattr(runtime_module,"create_chat_model",create)
            runtime=PlanningRuntime(snapshot.agent)
            async def invoke(name):
                state={'user_id':'u','project_id':'p','session_id':name,'run_id':name,'user_request':'hello',
                    'planning_revision_count':0,'project_system_prompt':'RULE_'+name,
                    'model_selection':snapshot.agent.model_catalog.select(name).model_dump()}
                context=AgentContext(project_system_prompt=state['project_system_prompt'],model_selection=state['model_selection'])
                if role=='conversation':return await runtime.respond(state,context,[])
                if role=='plan_revision':return await runtime.revise(state,context,[])
                return await runtime.execution_role(role.removeprefix('execution_'),state,role_payload(role))
            await asyncio.gather(*(invoke(name) for name in specs))
    assert constructions==["model-a","model-b"]
    assert {call["model"] for call in calls}=={"model-a","model-b"}
    for call in calls:
        own="alpha" if call["model"]=="model-a" else "beta"
        other="beta" if own=="alpha" else "alpha"
        assert "RULE_"+own in call["messages"][0]["content"]
        assert "RULE_"+other not in call["messages"][0]["content"]
        assert ("response_format" in call)==(own=="beta")


@pytest.mark.asyncio
async def test_executor_rejects_unavailable_selection_before_any_graph_work(monkeypatch):
    old=configured()
    pin=old.agent.model_catalog.select().model_dump()
    monkeypatch.setattr(service_settings,"_snapshot",configured("beta",MODEL_CATALOG={"beta":{"provider":"mock","model_name":"b"}}))
    graph=SimpleNamespace(ainvoke=AsyncMock(),aupdate_state=AsyncMock())
    loader=AsyncMock()
    adapter=GraphInvocation(graph,project_context_loader=loader,model_validator=validate_checkpoint_selection)
    with pytest.raises(ModelSelectionError):
        await adapter.invoke(None,{},values={"model_selection":pin},durability="sync")
    graph.ainvoke.assert_not_called()
    graph.aupdate_state.assert_not_called()
    loader.assert_not_called()
