"""Offline wheel smoke: run with python -I to exclude checkout imports.

Unpacks into a disposable directory; no dependency install or external service
calls. Existing interpreter dependencies must match the project lock.
"""

import asyncio
from importlib.resources import files
import json
from pathlib import Path
import sys
import tempfile
from uuid import uuid4
from zipfile import ZipFile

import argparse

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("wheel", type=Path)
wheel = parser.parse_args().wheel.resolve()
temporary = tempfile.TemporaryDirectory(prefix="dtest-layout-installed-")
installed = Path(temporary.name)
with ZipFile(wheel) as archive:
    names = archive.namelist()
    assert not any(
        "/tests/" in n or n.startswith("api_service/test/") for n in names
    )
    assert not any(n.startswith("app/") for n in names)
    assert all(
        any(n.startswith("dtest/" + package + "/") for n in names)
        for package in (
            "api_service",
            "agent_service",
            "worker_service",
            "application",
            "contracts",
            "infrastructure",
            "settings",
        )
    )
    assert not any(
        n.startswith(
            (
                "dtest/agent_service/agents/analysis/prompts/",
                "dtest/agent_service/agents/analysis/resources/",
            )
        )
        for n in names
    )
    assert (
        "dtest/agent_service/agents/analysis/workflow/workflows/workflow_lifecycle.md"
        in names
    )
    assert (
        "dtest/agent_service/agents/analysis/workflow/skills/generate_skill_index.py"
        in names
    )
    assert (
        "dtest/agent_service/agents/analysis/execution/grounding.py" in names
    )
    assert "dtest/agent_service/runtime/session_analysis.py" in names
    assert "dtest/agent_service/middleware/session_analysis.py" in names
    assert "dtest/agent_service/middleware/project_memory.py" in names
    assert "dtest/agent_service/runtime/memory_selection.py" in names
    assert "dtest/contracts/project_memory.py" in names
    assert "dtest/contracts/session_settings.py" in names
    assert "dtest/application/resources/project_memory.py" in names
    assert "dtest/infrastructure/memory/store.py" in names
    assert "dtest/application/resources/session_activity.py" in names
    assert "dtest/contracts/resources/session_activity_schema.py" in names
    assert "dtest/application/runs/public_status.py" in names
    assert "dtest/contracts/memory_store.py" in names
    assert "api_service/services/project_memory_service.py" not in names
    assert "api_service/models/common/project_memory_model.py" not in names
    assert "dtest/agent_service/middleware/planning_contract.py" in names
    assert (
        "dtest/agent_service/agents/analysis/agent_builders/conversation/planning_prompt.md"
        in names
    )
    assert (
        "dtest/agent_service/agents/analysis/workflow/tools/generate_tool_registry.py"
        in names
    )
    # Reject stale build/lib payloads even when the checkout no longer has them.
    for prefix in (
        "api_service/core/",
        "api_service/services/",
        "api_service/worker/",
        "api_service/agent_worker/",
        "api_service/observability/",
        "api_service/models/common/",
        "api_service/schemas/common/",
        "api_service/static/",
        "api_service/test/",
        "tests/",
    ):
        assert not any(name.startswith(prefix) for name in names), prefix
    for retired in (
        "api_service/runs/commands/migrate.py",
        "api_service/models/llm_run_model.py",
        "dtest/contracts/workflow.py",
    ):
        assert retired not in names, retired
    # Removed duplicate engines must never be revived by stale package build files.
    removed_root = "dtest/agent_service/agents/analysis/"
    for module in (
        "adaptive_workflow",
        "data_load_steps",
        "rule_based_notebook_generator",
        "workflow_compiler",
        "execution_notebook_reader",
    ):
        assert removed_root + "workflow/" + module + ".py" not in names
    assert removed_root + "resource_paths.py" not in names
    assert not any(
        name.startswith((removed_root + "schemas/", removed_root + "tools/"))
        for name in names
    )
    assert not any("/tmp/" in name for name in names)
    assert "dtest/api_service/web/static/demo.html" in names
    archive.extractall(installed)
sys.path.insert(0, str(installed))

import dtest.agent_service.agents.analysis.planning.graph as graph_module
from dtest.agent_service.agents.analysis.workflow.paths import (
    WORKFLOW_ROOT,
    TOOLS_ROOT,
    SKILL_INDEX_PATH,
    TOOL_REGISTRY_PATH,
)
from dtest.settings.agent import load_agent_settings
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from dtest.settings.loader import load_settings
from dtest.bootstrap import create_app
import dtest.devtools.analysis.cli

assert Path(graph_module.__file__).is_relative_to(installed)
assert SKILL_INDEX_PATH.is_file() and TOOL_REGISTRY_PATH.is_file()
assert (TOOLS_ROOT / "eda/profile_data.py").is_file()
assert WORKFLOW_ROOT.is_relative_to(installed.resolve())
roles = (
    "conversation",
    "execution_review",
    "execution_report",
    "execution_repair",
    "plan_revision",
)
for role in roles:
    prompt = files(
        f"dtest.agent_service.agents.analysis.agent_builders.{role}"
    ).joinpath("prompt.md")
    assert prompt.read_text(encoding="utf-8").strip()
# Construction only: exercise every production builder without calling a model.
production_settings = load_agent_settings(
    {
        "MODEL_PROVIDER": "openai_compatible",
        "MODEL_NAME": "package-smoke",
        "MODEL_API_KEY": "test-key",
        "API_BASE_URL": "http://llm.invalid/v1",
    }
)
from dtest.agent_service.factory import RoleAgent

# 064 keeps the current shared state schema; it is not the retired graph state.
assert "dtest/agent_service/agents/analysis/state.py" in names
retired = (
    "graph.py",
    "dependencies.py",
    "context.py",
    "hitl_protocol.py",
    "message_utils.py",
    "artifacts.py",
)
assert all(
    "dtest/agent_service/agents/analysis/" + name not in names
    for name in retired
)
assert not any(
    n.startswith(
        (
            "dtest/agent_service/agents/analysis/nodes/",
            "dtest/agent_service/agents/analysis/routers/",
            "dtest/agent_service/agents/analysis/components/",
            "dtest/agent_service/agents/analysis/testing/",
            "dtest/agent_service/agents/analysis/schemas/agents/",
        )
    )
    for n in names
)
assert all(
    "dtest/agent_service/agents/analysis/agent_builders/" + r + "/agent.py"
    not in names
    for r in (
        "routing",
        "intent_classifier",
        "faq",
        "report_writer",
        "skill_selector",
        "workflow_generator",
        "conditional_decider",
    )
)
from dtest.agent_service.agents.analysis.agent_builders.conversation.agent import (
    build_agent as build_conversation,
)
from dtest.agent_service.agents.analysis.planning.catalog import AssetCatalog
from dtest.agent_service.runtime.model_factory import create_chat_model

conversation = build_conversation(
    create_chat_model(production_settings), AssetCatalog()
)
assert (
    isinstance(conversation, RoleAgent)
    and conversation.agent.checkpointer is False
)
from dtest.agent_service.agents.analysis.agent_builders.execution_review.agent import (
    build_agent as build_review,
)
from dtest.agent_service.agents.analysis.agent_builders.execution_report.agent import (
    build_agent as build_report,
)
from dtest.agent_service.agents.analysis.agent_builders.execution_repair.agent import (
    build_agent as build_repair,
)

for builder in (build_review, build_report, build_repair):
    role = builder(create_chat_model(production_settings))
    assert isinstance(role, RoleAgent) and role.agent.checkpointer is False
from dtest.agent_service.agents.analysis.agent_builders.plan_revision.agent import (
    build_agent as build_revision,
)

revision = build_revision(
    create_chat_model(production_settings), AssetCatalog()
)
assert isinstance(revision, RoleAgent) and revision.agent.checkpointer is False
settings = load_settings(
    config={
        "MODEL_PROVIDER": "mock",
        "AGENT_WORKER_ENABLED": False,
        "EVENT_WORKER_ENABLED": False,
        "TASK_RECONCILER_ENABLED": False,
    },
    environ={},
)
app = create_app(settings)
paths = app.openapi()["paths"]
schemas = app.openapi()["components"]["schemas"]
assert not any(
    path.startswith(("/api/v1/jupyter-servers", "/api/v1/redis"))
    for path in paths
)
assert not any(
    "JupyterServer" in name or "RedisPing" in name for name in schemas
)
for retired in (
    "api_service/api/v1/routes/jupyter_servers.py",
    "api_service/api/v1/routes/redis.py",
    "api_service/services/jupyter_server_service.py",
    "api_service/services/redis_service.py",
    "api_service/schemas/common/jupyter_server_schema.py",
    "api_service/schemas/common/redis_schema.py",
    "api_service/models/common/jupyter_server_model.py",
):
    assert retired not in names
from dtest.settings.api import ApiSettings

assert not (
    {
        "jupyter_allowed_hosts",
        "jupyter_health_timeout_seconds",
        "jupyter_token_encryption_key",
        "redis_ping_timeout_seconds",
        "redis_host",
    }
    & ApiSettings.model_fields.keys()
)
from dtest.settings.redis import RedisSettings

assert "redis_url" in RedisSettings.model_fields
assert set(schemas["SessionCreate"]["properties"]) == {
    "session_name",
    "settings",
}
assert schemas["SessionCreate"]["additionalProperties"] is False
assert set(schemas["SessionSettings"]["properties"]) == {"kernel_profile"}
assert schemas["SessionSettings"]["additionalProperties"] is False
assert {"active_run", "availability"} <= set(
    schemas["SessionResource"]["required"]
)
assert set(schemas["SessionActiveRun"]["properties"]) == {"run_id", "status"}
assert set(schemas["SessionAvailability"]["properties"]) == {
    "status",
    "allowed_actions",
    "reason",
}
assert set(schemas["SessionUpdate"]["properties"]) == {"session_name"}
assert schemas["SessionUpdate"]["additionalProperties"] is False
assert (
    app.openapi()["components"]["securitySchemes"]["LoginSession"]["in"]
    == "cookie"
)
projects = paths["/api/v1/projects"]["get"]
assert projects["responses"]["200"]["content"]["application/json"]["schema"][
    "$ref"
].endswith("/Page_ProjectSummary_")
assert set(schemas["ProjectSummary"]["properties"]) == {
    "id",
    "name",
    "is_default",
    "created_at",
    "updated_at",
}
assert set(schemas["ProjectResource"]["properties"]) == {
    "id",
    "name",
    "is_default",
    "created_at",
    "updated_at",
    "system_prompt",
    "prompt_version",
}
assert schemas["ProjectCreate"]["additionalProperties"] is False
assert schemas["ProjectUpdate"]["additionalProperties"] is False
assert schemas["ProjectUpdate"]["minProperties"] == 1
for field in ("project_name", "system_prompt"):
    assert schemas["ProjectUpdate"]["properties"][field]["type"] == "string"
    assert "anyOf" not in schemas["ProjectUpdate"]["properties"][field]
    assert "default" not in schemas["ProjectUpdate"]["properties"][field]

assert "ProjectDeleteResult" not in schemas
assert "dtest/application/resources/project_queries.py" in names
users = paths["/api/v1/users"]["get"]
assert users["responses"]["200"]["content"]["application/json"]["schema"][
    "$ref"
].endswith("/Page_UserSummary_")
assert {
    "q",
    "role",
    "status",
    "limit",
    "cursor",
    "sort",
    "created_at_from",
    "created_at_to",
} == {p["name"] for p in users["parameters"] if p["in"] == "query"}
limit = next(p["schema"] for p in users["parameters"] if p["name"] == "limit")
assert limit["default"] == 50 and limit["maximum"] == 200
account_status = next(
    p["schema"] for p in users["parameters"] if p["name"] == "status"
)
assert account_status["default"] == "active" and set(
    account_status["enum"]
) == {"active", "deleted", "all"}
assert set(schemas["UserSummary"]["properties"]) == {
    "user_id",
    "user_name",
    "role",
    "is_active",
    "created_at",
    "updated_at",
    "deleted_at",
}
assert schemas["UserSummary"]["properties"]["user_id"]["type"] == "string"
assert "public_user_id" not in schemas["UserRead"]["properties"]
assert "dtest/application/resources/user_queries.py" in names
assert "/api/v1/projects/{project_id}/memory" in paths
assert "/api/v1/projects/{project_id}/memory/{section}/{key}" not in paths
assert {"get", "put", "delete"} <= set(
    paths["/api/v1/projects/{project_id}/memory"]
)
assert "/api/v1/auth/login/sso" in paths and "/api/v1/auth/logout" in paths
assert any(p.endswith("/runs") for p in paths)
assert not any("/tasks" in path for path in paths)
assert (
    "TaskResource" not in schemas and "TaskInvocationResource" not in schemas
)
for prefix in ("", "/admin"):
    for suffix in ("diagnostics", "invocations"):
        assert (
            "/api/v1"
            + prefix
            + "/sessions/{session_id}/runs/{run_id}/"
            + suffix
            in paths
        )
assert set(schemas["RunDiagnosticsResource"]["properties"]) == {
    "run_id",
    "session_id",
    "observed_at",
    "task",
    "session_work",
}
assert "invocation_id" in schemas["RunInvocationResource"]["properties"]
assert "run_id" in schemas["RunInvocationResource"]["properties"]
assert "public_run_id" not in schemas["RunInvocationResource"]["properties"]
for retired in (
    "api_service/api/v1/routes/tasks.py",
    "api_service/services/task_diagnostics.py",
    "api_service/schemas/common/task_schema.py",
):
    assert retired not in names
for suffix in ("cancel", "stream"):
    assert "/api/v1/tasks/{task_id}/" + suffix not in paths
    assert "/api/v1/sessions/{session_id}/runs/{run_id}/" + suffix in paths

assert "/api/v1/sessions/{session_id}/runs/{run_id}/resume" not in paths
assert "/api/v1/sessions/{session_id}/runs/{run_id}/join" not in paths
assert set(schemas["PublicRunSummary"]["properties"]) == {
    "run_id",
    "session_id",
    "status",
    "main_model_name",
    "model_revision",
    "recovery_required",
    "created_at",
    "updated_at",
    "started_at",
    "completed_at",
}
assert paths["/api/v1/sessions/{session_id}/runs"]["get"]["responses"]["200"][
    "content"
]["application/json"]["schema"]["$ref"].endswith("/Page_PublicRunSummary_")
logs = paths["/api/v1/sessions/{session_id}/runs/{run_id}/logs"]["get"]
assert logs["responses"]["200"]["content"]["application/json"]["schema"][
    "$ref"
].endswith("/Page_AgentRunLogResource_")
assert {
    "limit",
    "cursor",
    "sort",
    "created_at_from",
    "created_at_to",
    "agent_name",
    "node",
    "event",
    "kind",
} <= {p["name"] for p in logs["parameters"]}
limit = next(p["schema"] for p in logs["parameters"] if p["name"] == "limit")
assert limit["default"] == 50 and limit["maximum"] == 200
assert "/api/v1/sessions/{session_id}/runs/stream" in paths
assert (
    files("dtest.contracts")
    .joinpath("resources/workflow-definition.schema.json")
    .is_file()
)
assert (
    files("dtest.agent_service.agents.analysis.agent_builders.conversation")
    .joinpath("prompt.md")
    .is_file()
)
assert (
    files("dtest.agent_service.agents.analysis.planning")
    .joinpath("fixtures/quality-review.json")
    .is_file()
)


async def smoke():
    session = str(uuid4())
    cfg = {"configurable": {"thread_id": session}}
    from dtest.agent_service.agents.analysis.planning.runtime import (
        PlanningRuntime,
    )
    from dtest.agent_service.agents.analysis.planning.graph import (
        build_planning_graph,
    )

    planning_settings = load_settings(
        config={
            "MODEL_PROVIDER": "mock",
            "ANALYSIS_DATASETS": {
                "package-data": {
                    "title": "Package fixture",
                    "scope": "GLOBAL",
                    "runtime_path": "/workspace/pv/example.parquet",
                }
            },
        },
        environ={},
    )
    runtime = PlanningRuntime(planning_settings.agent)
    graph = build_planning_graph(runtime, checkpointer=InMemorySaver())
    state = await graph.ainvoke(
        {
            "user_id": str(uuid4()),
            "project_id": str(uuid4()),
            "session_id": session,
            "run_id": str(uuid4()),
            "user_request": "품질 분석 계획",
            "model_selection": runtime.models.select().model_dump(),
        },
        cfg,
    )
    plan = state["plan_views"][0]
    state = await graph.ainvoke(
        Command(
            resume={
                "resume": {
                    "action": "approve_plan",
                    "plan_id": plan["plan_id"],
                    "plan_revision": plan["plan_revision"],
                }
            }
        ),
        cfg,
    )
    assert (
        state["approved_snapshot"]["dataset_bindings"]["dataset"]["dataset_id"]
        == "package-data"
    )
    assert state["final_response"]["status"] == "plan_approved"
    assert not any(
        n == "dtest.agent_service.agents.analysis.dependencies"
        or n.startswith("dtest.agent_service.agents.analysis.nodes.")
        for n in sys.modules
    )
    from dtest.devtools.analysis.visualization import build_visualization_graph

    topology = build_visualization_graph()
    assert (
        "execution_wait" in topology.nodes
        and "execution_repair_propose" in topology.nodes
    )
    from dtest.devtools.analysis.cli import public_result

    assert "tool_sources" not in json.dumps(public_result(state))
    return len(state["approved_snapshot"]["steps"])


print(
    json.dumps(
        {
            "wheel": wheel.name,
            "source_checkout_imported": False,
            "project_crud_contract": True,
            "admin_user_read_contract": True,
            "session_settings_contract": True,
            "session_activity_contract": True,
            "api_openapi_paths": len(paths),
            "current_approved_plan_steps": asyncio.run(smoke()),
            "retired_graph_packages_absent": True,
            "unified_workflow_package_present": True,
            "tests_in_wheel": False,
            "resources_present": True,
            "role_prompts_present": len(roles),
            "production_builders_constructed": True,
            "create_agent_roles": len(roles),
            "role_checkpointers_disabled": True,
        }
    )
)
