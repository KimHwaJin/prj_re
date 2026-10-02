"""Tests for adapting durable Worker events to Agent execution state."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from langgraph.checkpoint.memory import InMemorySaver

from agent_config import load_agent_settings
from api_service.agent_worker.graph_provider import build_agent_graph


class RedisExecutionBoundaryTests(unittest.TestCase):
    def test_worker_graph_provider_builds_same_agentic_graph_for_redis_resume(self):
        settings = load_agent_settings({'MODEL_PROVIDER':'mock','EXECUTOR_SUBMIT_ENABLED':'true'})
        bindings = MagicMock()
        checkpointer = InMemorySaver()
        with (
            patch(
                "api_service.agent_worker.graph_provider.load_agent_settings",
                return_value=settings,
            ),
        ):
            graph = build_agent_graph(
                bindings=bindings,
                checkpointer=checkpointer,
                executor_client=MagicMock(),
            )

        self.assertIs(graph.checkpointer, checkpointer)
        self.assertEqual(graph.name, "agentic-planning-v1")
        self.assertIn("execution_wait", graph.nodes)

    def test_worker_and_api_builders_have_same_current_topology(self):
        from agent_service.agents.analysis.planning.runtime import PlanningRuntime
        from agent_service.agents.analysis.planning.graph import build_planning_graph
        settings=load_agent_settings({'MODEL_PROVIDER':'mock','EXECUTOR_SUBMIT_ENABLED':'true'})
        executor=MagicMock();bindings=MagicMock()
        with patch('api_service.agent_worker.graph_provider.load_agent_settings',return_value=settings):
            receiving=build_agent_graph(bindings=bindings,checkpointer=InMemorySaver(),executor_client=executor)
        current=build_planning_graph(PlanningRuntime(settings,executor=executor,bindings=bindings),checkpointer=InMemorySaver())
        assert set(receiving.nodes)==set(current.nodes)
        assert {(e.source,e.target) for e in receiving.get_graph().edges}=={(e.source,e.target) for e in current.get_graph().edges}
        assert 'wait_for_data_selection' not in receiving.nodes
