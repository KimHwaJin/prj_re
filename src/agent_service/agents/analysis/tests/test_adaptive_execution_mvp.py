"""MVP tests for raw observations, LLM decisions, and next code segments."""

from __future__ import annotations

import tempfile
import unittest

from agent_config import load_agent_settings
from agent_service.agents.analysis.dependencies import AgentDependencies
from agent_service.agents.analysis.workflow.data_load_steps import merge_required_data_load_steps
from agent_service.agents.analysis.workflow.workflow_compiler import compile_workflow_plan
from agent_service.agents.analysis.nodes.adaptive_execution import (
    make_build_next_adaptive_code,
    make_decide_conditional_tools,
)
from agent_service.agents.analysis.workflow.adaptive_workflow import decision_payload
from agent_service.agents.analysis.workflow.rule_based_notebook_generator import build_notebook


class DecisionAgent:
    def __init__(self, decision: str):
        self.decision = decision
        self.calls = []

    async def ainvoke(self, payload):
        self.calls.append(payload)
        candidate = payload["candidates"][0]
        return {
            "decisions": [
                {
                    "tool_id": candidate["tool_id"],
                    "decision": self.decision,
                    "reason": "Decision based on the raw condition Tool result.",
                }
            ]
        }


def _workflow() -> dict:
    data_argument = {
        "source": "step_output",
        "step_id": "load_data_1",
        "output": "data",
    }
    plan = {
        "plan_version": "1.0",
        "workflow": {
            "id": "adaptive-mvp",
            "name": "Adaptive MVP",
            "description": "Test manual adaptive execution.",
            "goal": "Conditionally impute missing values.",
            "status": "ready",
            "input_schema": {},
            "inputs": {},
            "input_provenance": {},
            "unresolved_inputs": [],
            "context": {},
            "steps": [
                {
                    "id": "quality",
                    "skill": "data_quality_check",
                    "depends_on": [],
                    "tools": [
                        {
                            "tool": name,
                            "selection_reason": "Required quality observation.",
                            "arguments": {"data": data_argument},
                        }
                        for name in (
                            "profile_data",
                            "compute_statistics",
                            "detect_outliers",
                        )
                    ],
                },
                {
                    "id": "cleaning",
                    "skill": "data_cleaning_pipeline",
                    "depends_on": ["quality"],
                    "tools": [
                        {
                            "tool": "impute_missing",
                            "selection_reason": "Impute only if missing data exists.",
                            "arguments": {"data": data_argument},
                        }
                    ],
                },
            ],
            "outputs": {
                "cleaned": {
                    "step_id": "cleaning",
                    "tool": "impute_missing",
                    "output": "imputed_data",
                }
            },
        },
    }
    document = compile_workflow_plan(
        plan,
        external_step_outputs={("load_data_1", "data")},
    )
    return merge_required_data_load_steps(
        document,
        {
            "data_count": 1,
            "datasets": [
                {
                    "role": "x", "data_type": "nce", "lot_cd": "6E2",
                    "process": ["ALL"], "query_mode": "period",
                    "start_dt": "2026-05-01", "end_dt": "2026-05-10",
                    "limit": 100, "transform_op": "pivot",
                }
            ],
        },
    )


class AdaptiveExecutionMvpTests(unittest.IsolatedAsyncioTestCase):
    def test_raw_condition_result_is_paired_with_pending_candidate(self):
        workflow = _workflow()
        initial = build_notebook(workflow, project_root="src")
        pending = initial["workflow"]["pending_conditional_tool_id"]
        raw_result = {"profile": {"missing_rate": 0.25, "raw": [1, 2, 3]}}

        payload = decision_payload(
            workflow,
            pending,
            {"quality.profile_data": raw_result},
        )

        candidate = payload["candidates"][0]
        self.assertEqual(candidate["condition_tool_id"], "quality.profile_data")
        self.assertEqual(candidate["condition_tool_result"], raw_result)

    async def test_include_decision_generates_next_code_segment(self):
        workflow = _workflow()
        initial = build_notebook(workflow, project_root="src")
        pending = initial["workflow"]["pending_conditional_tool_id"]
        decision_agent = DecisionAgent("include")
        deps = AgentDependencies(
            routing_agent=decision_agent,
            analysis_intent_agent=decision_agent,
            workflow_recommender=decision_agent,
            workflow_agent=decision_agent,
            faq_agent=decision_agent,
            file_lookup_agent=decision_agent,
            conditional_decision_agent=decision_agent,
        )
        state = {
            "workflow": workflow,
            "adaptive_pending_tool_id": pending,
            "adaptive_observations": {
                "quality.profile_data": {"profile": {"missing_rate": 0.25}}
            },
            "adaptive_runtime_decisions": {},
            "adaptive_decision_history": [],
        }
        decision_update = await make_decide_conditional_tools(deps)(state)
        chat_summary = decision_update["messages"][0]["content"]
        self.assertIn("조건 Tool:", chat_summary)
        self.assertIn("조건 결과 요약:", chat_summary)
        self.assertIn("결정: include", chat_summary)
        self.assertIn("이유:", chat_summary)
        self.assertIn("다음 실행 Tool:", chat_summary)
        state.update(decision_update)
        state.update(
            {
                "user_id": "user",
                "project_id": "project",
                "session_id": "session",
                "task_id": "task",
                "adaptive_executed_tool_ids": [
                    cell["tool_id"]
                    for cell in initial["cells"]
                    if cell.get("tool_id")
                ],
                "adaptive_round": 1,
                "artifact_files": {},
            }
        )
        with tempfile.TemporaryDirectory() as temporary_root:
            settings = load_agent_settings(
                {
                    "MODEL_NAME": "test",
                    "DEMO_ARTIFACTS_ENABLED": "false",
                    "DEMO_ARTIFACTS_ROOT": temporary_root,
                }
            )
            update = make_build_next_adaptive_code(settings)(state)

        self.assertEqual(
            update["adaptive_runtime_decisions"][pending]
            if "adaptive_runtime_decisions" in update
            else state["adaptive_runtime_decisions"][pending],
            "include",
        )
        self.assertIn(
            "impute_missing",
            [cell["tool"] for cell in update["notebook"]["cells"]],
        )
        self.assertIsNone(update["adaptive_pending_tool_id"])

    async def test_one_observation_resolves_consecutive_conditional_tools(self):
        data_argument = {
            "source": "step_output",
            "step_id": "load_data_1",
            "output": "data",
        }
        plan = {
            "plan_version": "1.0",
            "workflow": {
                "id": "eda-adaptive",
                "name": "EDA adaptive",
                "description": "Resolve two EDA candidates.",
                "goal": "Test shared condition observations.",
                "status": "ready",
                "input_schema": {},
                "inputs": {},
                "input_provenance": {},
                "unresolved_inputs": [],
                "context": {"output_dir": "outputs"},
                "steps": [
                    {
                        "id": "eda",
                        "skill": "eda_analysis",
                        "depends_on": [],
                        "tools": [
                            {
                                "tool": name,
                                "selection_reason": "Required EDA operation.",
                                "arguments": {"data": data_argument},
                            }
                            for name in (
                                "compute_statistics",
                                "histogram_eda",
                                "boxplot_eda",
                                "correlation_analysis",
                            )
                        ],
                    }
                ],
                "outputs": {
                    "histograms": {
                        "step_id": "eda",
                        "tool": "histogram_eda",
                        "output": "histogram_paths",
                    }
                },
            },
        }
        workflow = compile_workflow_plan(
            plan,
            external_step_outputs={("load_data_1", "data")},
        )
        workflow = merge_required_data_load_steps(
            workflow,
            {
                "data_count": 1,
                "datasets": [
                    {
                        "role": "x", "data_type": "nce", "lot_cd": "6E2",
                        "process": ["ALL"], "query_mode": "period",
                        "start_dt": "2026-05-01", "end_dt": "2026-05-10",
                        "limit": 100, "transform_op": "pivot",
                    }
                ],
            },
        )
        initial = build_notebook(workflow, project_root="src")
        for decision in ("include", "exclude"):
            with self.subTest(decision=decision):
                decision_agent = DecisionAgent(decision)
                deps = AgentDependencies(
                    routing_agent=decision_agent,
                    analysis_intent_agent=decision_agent,
                    workflow_recommender=decision_agent,
                    workflow_agent=decision_agent,
                    faq_agent=decision_agent,
                    file_lookup_agent=decision_agent,
                    conditional_decision_agent=decision_agent,
                )

                update = await make_decide_conditional_tools(deps)(
                    {
                        "workflow": workflow,
                        "task_id": "task",
                        "adaptive_pending_tool_id": initial["workflow"][
                            "pending_conditional_tool_id"
                        ],
                        "adaptive_observations": {
                            "eda.compute_statistics": {
                                "statistics": {"a": {}, "b": {}}
                            }
                        },
                        "adaptive_runtime_decisions": {},
                        "adaptive_decision_history": [],
                        "adaptive_executed_tool_ids": [
                            cell["tool_id"]
                            for cell in initial["cells"]
                            if cell.get("tool_id")
                        ],
                    }
                )

                self.assertEqual(len(decision_agent.calls), 2)
                self.assertEqual(
                    update["adaptive_runtime_decisions"],
                    {
                        "eda.boxplot_eda": decision,
                        "eda.correlation_analysis": decision,
                    },
                )


if __name__ == "__main__":
    unittest.main()
