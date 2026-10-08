"""Durable Executor settings, source precedence and service graph assembly."""

from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from langgraph.checkpoint.memory import InMemorySaver

import dtest.settings.loader as service_settings
from dtest import bootstrap
from dtest.container import ServiceContainer
from dtest.settings.loader import ConfigurationError, load_settings

VALUES = {
    "MODEL_PROVIDER": "mock",
    "AGENT_PROJECT_MEMORY_MODE": "off",
    "DATABASE_URL": "postgresql+asyncpg://localhost/service_test",
    "CHECKPOINT_DB_URI": "postgresql://localhost/checkpoint_test",
}
ERROR = "EXECUTOR_SUBMIT_ENABLED requires GRAPH_CHECKPOINTER=postgres"


@pytest.mark.parametrize("profile", ["local", "dev", "stg", "prd"])
def test_memory_submission_is_rejected_in_every_profile(profile):
    with pytest.raises(ConfigurationError, match=ERROR):
        load_settings(
            config={
                **VALUES,
                "GRAPH_CHECKPOINTER": "memory",
                "EXECUTOR_SUBMIT_ENABLED": True,
            },
            environ={},
            profile=profile,
        )


@pytest.mark.parametrize("source", ["config", "environment", "dotenv"])
def test_final_typed_settings_reject_invalid_combination(tmp_path, source):
    values = {
        "GRAPH_CHECKPOINTER": "memory",
        "EXECUTOR_SUBMIT_ENABLED": "true",
    }
    with pytest.raises(ConfigurationError, match=ERROR):
        if source == "config":
            load_settings(config=values, environ={})
        elif source == "environment":
            load_settings(config={}, environ=values)
        else:
            dotenv = tmp_path / "local.env"
            dotenv.write_text(
                "GRAPH_CHECKPOINTER=memory\nEXECUTOR_SUBMIT_ENABLED=true\n"
            )
            load_settings(config={}, environ={}, dotenv_path=dotenv)


@pytest.mark.parametrize(
    "config,environ,expected",
    [
        (
            {"GRAPH_CHECKPOINTER": "postgres"},
            {
                "GRAPH_CHECKPOINTER": "memory",
                "EXECUTOR_SUBMIT_ENABLED": "true",
            },
            ("postgres", True),
        ),
        (
            {"EXECUTOR_SUBMIT_ENABLED": False},
            {
                "GRAPH_CHECKPOINTER": "memory",
                "EXECUTOR_SUBMIT_ENABLED": "true",
            },
            ("memory", False),
        ),
        (
            {"GRAPH_CHECKPOINTER": "memory"},
            {"EXECUTOR_SUBMIT_ENABLED": "false"},
            ("memory", False),
        ),
    ],
)
def test_config_precedence_applies_before_combination_check(
    config, environ, expected
):
    settings = load_settings(config=config, environ=environ)
    assert (
        settings.agent.graph_checkpointer,
        settings.agent.executor_submit_enabled,
    ) == expected


def test_invalid_yaml_combination_never_falls_back_to_environment():
    with pytest.raises(ConfigurationError, match=ERROR):
        load_settings(
            config={
                "GRAPH_CHECKPOINTER": "memory",
                "EXECUTOR_SUBMIT_ENABLED": True,
                "MODEL_API_KEY": "private-token",
            },
            environ={
                "GRAPH_CHECKPOINTER": "postgres",
                "EXECUTOR_SUBMIT_ENABLED": "false",
            },
        )


@pytest.mark.parametrize("check_config", [False, True])
def test_launcher_rejects_before_app_or_resources(
    tmp_path, monkeypatch, check_config
):
    path = tmp_path / "config.yml"
    path.write_text(
        "GRAPH_CHECKPOINTER: memory\nEXECUTOR_SUBMIT_ENABLED: true\n"
        "PRIVATE_LLM_API_KEY: private-token\n"
    )
    original = bootstrap.load_settings
    monkeypatch.setattr(
        bootstrap, "load_settings", lambda **kw: original(environ={}, **kw)
    )
    app = Mock(side_effect=AssertionError("App must not start"))
    monkeypatch.setattr(bootstrap, "create_app", app)
    args = ["--env", "local", "--config", str(path)]
    if check_config:
        args.append("--check-config")
    with pytest.raises(ConfigurationError, match=ERROR) as caught:
        bootstrap.main(args)
    assert "private-token" not in str(caught.value)
    app.assert_not_called()


class CheckpointDouble(InMemorySaver):
    """Compiled graph test double; no PostgreSQL connection is opened."""

    def __init__(self):
        super().__init__()
        self.conn = object()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,submit", [("memory", False), ("postgres", False), ("postgres", True)]
)
async def test_supported_settings_build_expected_graph_resources(
    monkeypatch, kind, submit
):
    import dtest.agent_service.runtime.langgraph.checkpointer as checkpoint
    import dtest.infrastructure.database.executor_bindings as bindings
    import dtest.infrastructure.executor.client as executor
    import dtest.infrastructure.workflow_search.runtime as search

    settings = load_settings(
        config={
            **VALUES,
            "GRAPH_CHECKPOINTER": kind,
            "EXECUTOR_SUBMIT_ENABLED": submit,
        },
        environ={},
    )
    monkeypatch.setattr(service_settings, "_snapshot", settings)
    monkeypatch.setattr(
        search, "get_workflow_runtime", lambda: SimpleNamespace(search=None)
    )
    events = []
    saver = CheckpointDouble()

    @asynccontextmanager
    async def open_checkpoint(**kwargs):
        assert kwargs["database_url"] == settings.agent.checkpoint_db_uri
        events.append("open_checkpoint")
        try:
            yield saver
        finally:
            events.append("close_checkpoint")

    @asynccontextmanager
    async def open_executor(agent_settings):
        assert agent_settings is settings.agent
        events.append("open_executor")
        try:
            yield object()
        finally:
            events.append("close_executor")

    @asynccontextmanager
    async def open_bindings(worker_settings):
        assert worker_settings is settings.worker
        events.append("open_bindings")
        try:
            yield SimpleNamespace(bindings=object(), pool=object())
        finally:
            events.append("close_bindings")

    monkeypatch.setattr(checkpoint, "create_checkpointer", open_checkpoint)
    monkeypatch.setattr(executor, "ExecutorClient", open_executor)
    monkeypatch.setattr(bindings, "ApiWorkerBridge", open_bindings)
    container = ServiceContainer()
    owner = SimpleNamespace(
        _load_graph_inputs=container.load_inputs,
        _worker_settings=lambda: settings.worker,
    )
    names = [] if kind == "memory" else ["checkpoint"]
    if submit:
        names += ["executor", "bindings"]
    async with container.open_graph(owner) as graph:
        assert ("execution_select" in graph.nodes) is submit
        assert isinstance(graph.checkpointer, InMemorySaver)
        if kind == "postgres":
            assert graph.checkpointer is saver
        assert events == ["open_" + name for name in names]
    assert events == ["open_" + name for name in names] + [
        "close_" + name for name in reversed(names)
    ]


@pytest.mark.asyncio
async def test_composition_rejects_bypassed_settings_before_external_resources(
    monkeypatch,
):
    import dtest.infrastructure.workflow_search.runtime as search

    settings = load_settings(config=VALUES, environ={})
    invalid = replace(
        settings.agent,
        graph_checkpointer="memory",
        executor_submit_enabled=True,
    )
    resources = Mock(side_effect=AssertionError("No resources may open"))
    monkeypatch.setattr(search, "get_workflow_runtime", resources)
    owner = SimpleNamespace(
        _load_graph_inputs=lambda: (object(), invalid, "memory")
    )
    with pytest.raises(ConfigurationError, match=ERROR):
        async with ServiceContainer().open_graph(owner):
            pytest.fail("An invalid execution graph was assembled")
    resources.assert_not_called()


def test_installing_manually_built_snapshot_rejects_invalid_combination(
    monkeypatch,
):
    settings = load_settings(config=VALUES, environ={})
    invalid = replace(
        settings,
        agent=replace(
            settings.agent,
            graph_checkpointer="memory",
            executor_submit_enabled=True,
        ),
    )
    monkeypatch.setattr(service_settings, "_snapshot", None)
    with pytest.raises(ConfigurationError, match=ERROR):
        service_settings.configure(invalid)
    assert service_settings._snapshot is None
