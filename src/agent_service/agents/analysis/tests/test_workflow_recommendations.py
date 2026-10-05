"""create_agent integration: scope, immutable references and concurrent calls."""

import asyncio
import json
from copy import deepcopy
import httpx
import pytest
from service_contracts.workflow_retrieval import (
    WorkflowSearchResult,
    WorkflowSearchCandidate,
    WorkflowSearchDiagnostics,
)
from agent_service.agents.analysis.agent_builders.conversation.agent import (
    build_agent,
    reply_schema,
)
from agent_service.agents.analysis.tests.asset_fixtures import assets
from agent_service.agents.analysis.tests.test_workflow_standard import public_document
from agent_service.agents.analysis.tests.test_conversation_performance import (
    model,
    response,
)
from agent_service.context import AgentContext


class Retriever:
    def __init__(self, document):
        self.document = document
        self.calls = []

    async def search(self, query):
        self.calls.append(query)
        await asyncio.sleep(0)
        return WorkflowSearchResult(
            items=[
                WorkflowSearchCandidate(
                    workflow_id="wf.catalog-" + query,
                    content_sha256="a" * 64,
                    resource_revision=3,
                    search_revision=2,
                    name="Catalog example",
                    similarity=0.91,
                    matched_query="similar registered request",
                    document=deepcopy(self.document),
                )
            ],
            diagnostics=WorkflowSearchDiagnostics(
                termination="candidate_limit", distinct_candidates=1, reranked=True
            ),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("asset", ["inventory", "billing"])
@pytest.mark.parametrize("mode", ["prompt_json", "provider_json_schema"])
async def test_full_analysis_selects_pinned_template_without_mutation(
    tmp_path, asset, mode
):
    catalog, case = assets(tmp_path / asset, asset)
    document = public_document(case, adaptive=True)
    document["workflow"]["execution"] = {
        "mode": "MULTI",
        "repair_level": 1,
        "max_repair_attempts": 1,
    }
    # Public standard execution contract is checked by the deployed catalog.
    retriever = Retriever(document)
    calls = []

    async def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            value = {
                "kind": "planning",
                "planning_scope": "end_to_end",
                "message": "계획 검색",
                "skill_ids": [case["skill"]],
                "plans": [],
            }
        else:
            value = {
                "kind": "plans",
                "message": "검증된 계획입니다.",
                "plans": [
                    {
                        "workflow_id": "wf.catalog-current",
                        "input_values": {"payload": "[2,4]"},
                    }
                ],
            }
        return response({"role": "assistant", "content": json.dumps(value)})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await build_agent(
            model(client),
            catalog,
            workflow_retriever=retriever,
            structured_output_mode=mode,
        ).ainvoke({"request": "current"}, context=AgentContext())
    assert retriever.calls == ["current"] and len(calls) == 2
    proposal = result.plans[0]
    assert (
        proposal.definition["workflow_id"] == "wf.catalog-current"
        and proposal.definition["definition_version"] == 3
    )
    assert proposal.definition["execution"]["repair_level"] == 1
    assert proposal._catalog_reference["content_sha256"] == "a" * 64
    assert all(
        t["function"]["name"] != "search_workflows" for t in calls[1].get("tools", [])
    )


@pytest.mark.asyncio
async def test_faq_and_incremental_do_not_search_and_sessions_are_isolated(tmp_path):
    catalog, case = assets(tmp_path / "assets", "inventory")
    retriever = Retriever(public_document(case))
    calls = {}

    async def handle(request):
        body = json.loads(request.content)
        payload = json.loads(
            next(m["content"] for m in body["messages"] if m["role"] == "user")
        )
        query = payload["request"]
        calls[query] = calls.get(query, 0) + 1
        if query == "faq":
            value = {"kind": "answer", "message": "일반 답변", "plans": []}
        elif calls[query] == 1:
            value = {
                "kind": "planning",
                "planning_scope": "incremental" if query == "partial" else "end_to_end",
                "message": "계획",
                "skill_ids": [case["skill"]],
                "plans": [],
            }
        elif query == "partial":
            value = {
                "kind": "answer",
                "message": "부분 요청을 확인해 주세요.",
                "plans": [],
            }
        else:
            value = {
                "kind": "plans",
                "message": "추천",
                "plans": [
                    {
                        "workflow_id": "wf.catalog-" + query,
                        "input_values": {"payload": "[2,4]"},
                    }
                ],
            }
        return response({"role": "assistant", "content": json.dumps(value)})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        agent = build_agent(model(client), catalog, workflow_retriever=retriever)
        values = await asyncio.gather(
            *(
                agent.ainvoke({"request": q}, context=AgentContext(session_id=q))
                for q in ["faq", "partial", "full-a", "full-b"]
            )
        )
    assert sorted(retriever.calls) == ["full-a", "full-b"]
    assert [r.plans[0].definition["workflow_id"] for r in values if r.plans] == [
        "wf.catalog-full-a",
        "wf.catalog-full-b",
    ]


def test_combined_limit_and_definition_reference_exclusion(tmp_path):
    catalog, _ = assets(tmp_path / "assets", "billing")
    schema = reply_schema(catalog, 2)
    with pytest.raises(ValueError, match="at most 2"):
        schema.model_validate(
            {
                "kind": "plans",
                "message": "plans",
                "plans": [{"workflow_id": str(i)} for i in range(3)],
            }
        )
    with pytest.raises(ValueError, match="exactly one"):
        schema.model_validate(
            {
                "kind": "plans",
                "message": "plans",
                "plans": [{"workflow_id": "template", "definition": {}}],
            }
        )


@pytest.mark.asyncio
async def test_catalog_reference_survives_user_edit_and_frozen_approval(tmp_path):
    from uuid import uuid4
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command
    from service_settings import load_settings
    from agent_service.agents.analysis.planning.runtime import PlanningRuntime
    from agent_service.agents.analysis.planning.graph import build_planning_graph
    from service_contracts.plan_interaction import InteractionData

    catalog, case = assets(tmp_path / "assets", "billing")
    settings = load_settings(config={"MODEL_PROVIDER": "mock"}, environ={}).agent
    retriever = Retriever(public_document(case))
    calls = []

    async def handle(request):
        calls.append(request)
        value = (
            {
                "kind": "planning",
                "planning_scope": "end_to_end",
                "message": "계획",
                "skill_ids": [case["skill"]],
                "plans": [],
            }
            if len(calls) == 1
            else {
                "kind": "plans",
                "message": "추천",
                "plans": [
                    {
                        "workflow_id": "wf.catalog-current",
                        "input_values": {"payload": "[2,4]"},
                    }
                ],
            }
        )
        return response({"role": "assistant", "content": json.dumps(value)})

    runtime = PlanningRuntime(settings, catalog=catalog, workflow_retriever=retriever)
    selected = runtime.models.select().model_dump()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        runtime.agents[(selected["name"], selected["revision"])] = build_agent(
            model(client), catalog, workflow_retriever=retriever
        )
        graph = build_planning_graph(runtime, checkpointer=InMemorySaver())
        values = {
            key: str(uuid4())
            for key in ["user_id", "project_id", "session_id", "run_id"]
        }
        config = {"configurable": {"thread_id": values["session_id"]}}
        waiting = await graph.ainvoke(
            {**values, "user_request": "current", "model_selection": selected}, config
        )
        interaction = InteractionData.model_validate(waiting["interaction_data"])
        plan = interaction.payload.plans[0]
        assert plan.catalog_reference.resource_revision == 3
        # User edits the parameter after search; it remains the pinned revision.
        review = waiting["reviews"][0]
        action = {
            "action": "approve_plan",
            "plan_id": review["plan_id"],
            "plan_revision": 1,
            "input_values": {"payload": "[6,8]"},
        }
        completed = await graph.ainvoke(Command(resume={"resume": action}), config)
        snapshot = completed["approved_snapshot"]
        assert snapshot["catalog_reference"]["resource_revision"] == 3
        assert snapshot["catalog_reference"]["content_sha256"] == "a" * 64
        assert snapshot["input_values"]["payload"] == "[6,8]"
        assert completed["final_response"]["status"] == "plan_approved"
        assert retriever.calls == ["current"]


@pytest.mark.asyncio
async def test_unavailable_search_generates_new_plan_and_unreturned_id_rejected(
    tmp_path,
):
    from agent_service.agents.analysis.planning.recommendations import (
        resolve_recommendations,
    )

    catalog, case = assets(tmp_path / "assets", "inventory")
    schema = reply_schema(catalog, 5)
    value = schema.model_validate(
        {
            "kind": "plans",
            "message": "unknown",
            "plans": [{"workflow_id": "not-returned"}],
        }
    )
    with pytest.raises(ValueError, match="returned by this invocation"):
        resolve_recommendations(value, [], catalog, repair_limit=4, repair_attempts=3)

    class Empty:
        async def search(self, query):
            return WorkflowSearchResult(
                diagnostics=WorkflowSearchDiagnostics(termination="index_unavailable")
            )

    calls = []

    async def handle(request):
        calls.append(request)
        definition = __import__(
            "service_contracts.workflow_standard", fromlist=["normalize"]
        ).normalize(public_document(case), catalog.metadata)
        value = (
            {
                "kind": "planning",
                "planning_scope": "end_to_end",
                "message": "계획",
                "skill_ids": [case["skill"]],
                "plans": [],
            }
            if len(calls) == 1
            else {
                "kind": "plans",
                "message": "새 계획",
                "plans": [
                    {"definition": definition, "input_values": {"payload": "[2,4]"}}
                ],
            }
        )
        return response({"role": "assistant", "content": json.dumps(value)})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        reply = await build_agent(
            model(client), catalog, workflow_retriever=Empty()
        ).ainvoke({"request": "current"}, context=AgentContext())
    assert (
        reply.plans[0].workflow_id is None and reply.plans[0]._catalog_reference is None
    )
