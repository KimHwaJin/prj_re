"""Turn budgets preserve whole request/HITL exchanges and checkpoint state."""

from types import SimpleNamespace
from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from dtest.agent_service.runtime.conversation_history import (
    append_history,
    history_for_prompt,
    bounded_history,
)
from dtest.agent_service.context import AgentContext
from dtest.agent_service.factory import RoleAgent
from dtest.agent_service.agents.analysis.planning.graph import (
    build_planning_graph,
)
from tests.agent_service.test_planning_runtime import setup, resume


def test_limit_counts_complete_turns_including_multiple_hitl_feedback_messages():
    settings = SimpleNamespace(set_max_history=2, active_multi_turn=True)
    history = []
    for turn in range(5):
        for role, content in (
            ("user", "question"),
            ("assistant", "proposal"),
            ("user", "change parameters"),
            ("assistant", "revised"),
        ):
            history = append_history(
                history,
                role=role,
                content=f"{turn}:{content}",
                turn_id=turn,
                settings=settings,
            )
    assert len(history) == 12  # 2 previous turns + current, not2 messages
    assert {item["turn_id"] for item in history} == {"2", "3", "4"}
    prompt = history_for_prompt({"history": history}, settings)
    assert len(prompt) == 12 and all(
        set(item) == {"role", "content"} for item in prompt
    )


def test_disabled_history_and_zero_budget_keep_current_run_feedback():
    history = [
        {"role": "user", "content": "old", "turn_id": "old"},
        {"role": "assistant", "content": "old answer", "turn_id": "old"},
        {"role": "user", "content": "current", "turn_id": "current"},
        {"role": "assistant", "content": "proposal", "turn_id": "current"},
        {"role": "user", "content": "replan", "turn_id": "current"},
    ]
    for settings in (
        SimpleNamespace(set_max_history=6, active_multi_turn=False),
        SimpleNamespace(set_max_history=0, active_multi_turn=True),
    ):
        prompt = history_for_prompt({"history": history}, settings)
        assert [item["content"] for item in prompt] == [
            "current",
            "proposal",
            "replan",
        ]
    assert (
        history[0]["content"] == "old"
    )  # prompt policy never mutates checkpoints


def test_legacy_checkpoints_keep_whole_pairs_and_ignore_internal_messages():
    history = [
        {"role": "assistant", "content": "orphan"},
        {"role": "system", "content": "internal"},
    ]
    history += [
        item
        for turn in range(3)
        for item in (
            {"role": "user", "content": f"q{turn}"},
            {"role": "assistant", "content": f"a{turn}"},
        )
    ]
    assert [
        item["content"] for item in bounded_history(history, previous_turns=1)
    ] == ["q1", "a1", "q2", "a2"]


@pytest.mark.asyncio
async def test_real_graph_keeps_six_previous_turns_plus_current():
    runtime, value, config = setup()
    runtime.settings = replace(runtime.settings, set_max_history=6)
    graph = build_planning_graph(runtime, checkpointer=InMemorySaver())
    for i in range(10):
        run_id = str(uuid4())
        state = await graph.ainvoke(
            {
                **value,
                "run_id": run_id,
                "user_request": f"[answer] question{i}",
                "initial_request_identity": {"command_id": run_id},
            },
            config,
        )
    assert len(state["history"]) == 14
    assert state["history"][0]["content"] == "[answer] question3"
    assert len({item["turn_id"] for item in state["history"]}) == 7


@pytest.mark.asyncio
async def test_real_hitl_replan_stays_in_same_turn_when_history_is_disabled():
    runtime, value, config = setup()
    runtime.settings = replace(
        runtime.settings, active_multi_turn=False, set_max_history=0
    )
    graph = build_planning_graph(runtime, checkpointer=InMemorySaver())
    state = await graph.ainvoke(value, config)
    from dtest.agent_service.agents.analysis.planning.proposals import (
        RevisionReply,
    )

    async def revise(current, *args):
        return RevisionReply(
            kind="plans",
            message="계획을 다시 확인해 주세요.",
            plans=[
                {
                    "base_plan_id": current["reviews"][0]["plan_id"],
                    "patches": [],
                }
            ],
        )

    runtime.revise = revise
    state = await resume(
        graph,
        config,
        state,
        {
            "action": "replan",
            "interaction_id": state["interaction_id"],
            "revision": state["interaction_revision"],
            "feedback": "다른 계획을 보여줘",
        },
    )
    assert state["__interrupt__"] and state["user_resume_receipt"]
    assert {item["turn_id"] for item in state["history"]} == {value["run_id"]}
    assert len(history_for_prompt(state, runtime.settings)) == 4
    plan = state["plan_views"][0]
    approved = await resume(
        graph,
        config,
        state,
        {
            "action": "approve_plan",
            "plan_id": plan["plan_id"],
            "plan_revision": plan["plan_revision"],
        },
    )
    assert approved["approved_snapshot"] and not approved.get("__interrupt__")


@pytest.mark.asyncio
async def test_runtime_context_passes_configured_budget_to_inner_role():
    runtime, value, _ = setup()
    runtime.settings = replace(runtime.settings, recursion_limit=47)
    context = runtime.bind_context(value, AgentContext())
    backend = SimpleNamespace(ainvoke=AsyncMock(return_value={"messages": []}))
    await RoleAgent(backend).ainvoke({}, context=context)
    assert backend.ainvoke.call_args.kwargs["config"]["recursion_limit"] == 47


def test_first_resume_of_legacy_checkpoint_keeps_current_question_and_feedback():
    settings = SimpleNamespace(set_max_history=0, active_multi_turn=False)
    old = [
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "current"},
        {"role": "assistant", "content": "proposal"},
    ]
    updated = append_history(
        old,
        role="user",
        content="revise",
        turn_id="run",
        settings=settings,
        initial_request="current",
    )
    assert [item["content"] for item in updated] == [
        "current",
        "proposal",
        "revise",
    ]
    assert {item["turn_id"] for item in updated} == {"run"}
    assert all("turn_id" not in item for item in old)
