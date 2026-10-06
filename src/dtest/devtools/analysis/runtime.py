"""Isolated mock entrypoint for the same graph builder used by API and Worker.

No deployment config, .env, PostgreSQL, Redis, Executor or SSO is loaded. Use the
service API for integration tests; this helper only inspects local graph behavior.
"""
from uuid import uuid4
from dtest.settings.agent import load_agent_settings
from dtest.agent_service.agents.analysis.planning.runtime import PlanningRuntime
from dtest.agent_service.agents.analysis.planning.graph import build_planning_graph


def local_runtime(*, delay_ms=0, executor=None, bindings=None):
    settings = load_agent_settings({
        "MODEL_PROVIDER": "mock", "MODEL_MOCK_DELAY_MS": str(delay_ms),
        "EXECUTOR_SUBMIT_ENABLED": "true" if executor is not None else "false",
        "AGENT_PROJECT_MEMORY_MODE": "off",
        "ANALYSIS_DATASETS": '{"local-example":{"title":"Offline example","scope":"GLOBAL","runtime_path":"/workspace/pv/example.parquet"}}',
    })
    return PlanningRuntime(settings, executor=executor, bindings=bindings)


def local_input(runtime, request):
    value = {key: str(uuid4()) for key in ("user_id", "project_id", "session_id", "run_id")}
    value.update(user_request=request, model_selection=runtime.models.select().model_dump())
    from dtest.contracts.initial_request import initial_identity
    value["initial_request_identity"] = initial_identity(value)
    return value


def compiled_in_memory_graph(runtime=None):
    from langgraph.checkpoint.memory import InMemorySaver
    return build_planning_graph(runtime or local_runtime(), checkpointer=InMemorySaver())
