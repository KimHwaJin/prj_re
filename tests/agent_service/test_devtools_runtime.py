"""Developer entrypoints inspect the production graph without external resources."""

import json
import runpy
from pathlib import Path
import pytest
from langgraph.types import Command
from dtest.devtools.analysis.runtime import (
    local_runtime,
    local_input,
    compiled_in_memory_graph,
)
from dtest.devtools.analysis.visualization import (
    build_visualization_graph,
    graph_to_mermaid,
)
from dtest.devtools.analysis.cli import public_result


def test_offline_helper_ignores_deployment_environment(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "openai_compatible")
    monkeypatch.setenv("MODEL_NAME", "must-not-use")
    monkeypatch.setenv("EXECUTOR_SUBMIT_ENABLED", "true")
    monkeypatch.setenv("AGENT_PROJECT_MEMORY_MODE", "auto_context")
    runtime = local_runtime()
    assert (
        runtime.models.resolve(runtime.models.select().model_dump()).provider
        == "mock"
    )
    assert (
        not runtime.execution_enabled and runtime.memory_policy_factory is None
    )
    assert runtime.settings.agent_project_memory_mode == "off"


@pytest.mark.asyncio
async def test_offline_graph_uses_typed_plan_edit_and_approval_without_executor():
    runtime = local_runtime()
    graph = compiled_in_memory_graph(runtime)
    value = local_input(runtime, "quality review")
    cfg = {"configurable": {"thread_id": value["session_id"]}}
    state = await graph.ainvoke(value, cfg, durability="sync")
    plan = state["plan_views"][0]
    command = {
        "resume": {
            "action": "edit_plan",
            "plan_id": plan["plan_id"],
            "plan_revision": 1,
            "step_changes": [
                {"step_id": "outliers", "parameter": "method", "value": "iqr"}
            ],
        }
    }
    state = await graph.ainvoke(
        Command(resume=command), cfg, durability="sync"
    )
    plan = state["plan_views"][0]
    assert plan["plan_revision"] == 2
    state = await graph.ainvoke(
        Command(
            resume={
                "resume": {
                    "action": "approve_plan",
                    "plan_id": plan["plan_id"],
                    "plan_revision": 2,
                }
            }
        ),
        cfg,
        durability="sync",
    )
    assert state["final_response"][
        "status"
    ] == "plan_approved" and not state.get("execution_id")
    shown = json.dumps(public_result(state))
    assert "def data_load" not in shown and "tool_sources" not in shown
    assert next(iter(runtime.agents.values())).calls == 1


def test_visualization_includes_current_execution_and_repair_without_connections():
    graph = build_visualization_graph()
    diagram = graph_to_mermaid(graph)
    assert graph.name == "agentic-planning-v1"
    assert {
        "execution_wait",
        "execution_decision_wait",
        "execution_repair_wait",
        "revise_plan",
    } <= graph.nodes.keys()
    assert (
        "wait_for_data_selection" not in diagram
        and "execution_wait" in diagram
    )
    edges = {(e.source, e.target) for e in graph.get_graph().edges}
    assert {
        ("conversation", "publish_review"),
        ("apply_review", "execution_select"),
        ("execution_process_event", "execution_repair_propose"),
        ("execution_review", "execution_decision_wait"),
        ("execution_repair_applied", "execution_process_event"),
    } <= edges
    assert ("execution_review", "execution_submit") not in edges


def test_studio_entrypoint_has_current_graph_without_importing_api_or_services():
    path = Path(__file__).resolve().parents[2] / "langgraph_dev.py"
    graph = runpy.run_path(str(path))["graph"]
    assert (
        graph.name == "agentic-planning-v1" and "conversation" in graph.nodes
    )
    assert "execution_submit" not in graph.nodes and graph.checkpointer is None
    config = json.loads(path.with_name("langgraph.json").read_text())
    assert set(config) == {"dependencies", "graphs"}
