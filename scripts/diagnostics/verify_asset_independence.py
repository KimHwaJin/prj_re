"""Real-model discovery/planning over two isolated, non-shipped asset pools.

No application database, API authentication, Redis worker or Executor is involved.
The asset contents are synthetic, model replies are real. Raw replies are saved
privately and must not be confused with an end-to-end service verification.
"""

import argparse
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from dtest.agent_service.agents.analysis.planning.graph import (
    build_planning_graph,
)
from dtest.agent_service.agents.analysis.planning.runtime import (
    PlanningRuntime,
)
from tests.agent_service.asset_fixtures import assets
from dtest.contracts.user_resume import resume_identity, resume_envelope
from dtest.settings.loader import load_settings
from model_connection import load_model_env, model_host_alias
from cookie_auth import write_private_result


async def verify(model, output):
    report = {
        "model": "real",
        "executor": "not_called",
        "assets": "isolated_synthetic",
        "cases": [],
    }
    with tempfile.TemporaryDirectory(prefix="asset-independent-") as folder:
        for name in ["inventory", "billing"]:
            item = {"name": name, "checks": []}
            report["cases"].append(item)

            def check(label, condition):
                item["checks"].append(
                    {"name": label, "passed": bool(condition)}
                )
                print(
                    json.dumps(
                        {
                            "case": name,
                            "check": label,
                            "passed": bool(condition),
                        }
                    ),
                    flush=True,
                )

            try:
                catalog, case = assets(Path(folder) / name, name)
                settings = load_settings(
                    config={
                        **model,
                        "EXECUTOR_SUBMIT_ENABLED": False,
                        "AGENT_PROJECT_MEMORY_MODE": "off",
                        "MODEL_MAX_RETRIES": 0,
                    },
                    environ={},
                ).agent
                runtime = PlanningRuntime(settings, catalog=catalog)
                graph = build_planning_graph(
                    runtime, checkpointer=InMemorySaver()
                )
                value = {
                    k: str(uuid4())
                    for k in ("user_id", "project_id", "session_id", "run_id")
                }
                value.update(
                    user_request=f'{name} Skill의 등록된 Tool들을 이용해 JSON 문자열 "[2,4,6]"을 해석하고 '
                    f"그 배열의 합계에 {case['option']}=3을 적용하는 실행 계획을 작성해줘. "
                    "입력과 배율은 승인 화면에서 내가 수정할 수 있어야 하고 Markdown 보고서도 필요해. "
                    "등록된 함수의 반환 구조를 읽고 단계 출력을 연결해줘. 아직 실행하지 말고 승인받아.",
                    model_selection=runtime.models.select().model_dump(),
                )
                config = {"configurable": {"thread_id": value["session_id"]}}
                async with asyncio.timeout(240):
                    state = await graph.ainvoke(
                        value, config, durability="sync"
                    )
                item["reply"] = deepcopy(
                    state.get("plan_views") or state.get("final_response")
                )
                views = state.get("plan_views", [])
                check("real_model_returned_plan", bool(views))
                if not views:
                    continue
                review = state["reviews"][0]
                doc = review["document"]
                item["document"] = deepcopy(doc)
                check(
                    "only_alternate_registered_tools",
                    {s["tool_id"] for s in doc["steps"]}
                    == set(catalog.sources),
                )
                reader = next(
                    s for s in doc["steps"] if s["tool_id"] == case["reader"]
                )
                processor = next(
                    s
                    for s in doc["steps"]
                    if s["tool_id"] == case["processor"]
                )
                ref = reader["arguments"][case["reader_arg"]]
                obj = processor["arguments"][case["object_arg"]]
                check(
                    "declared_input_binding",
                    ref["source"] == "workflow_input"
                    and doc["inputs"][ref["name"]]["kind"] == "parameter",
                )
                check(
                    "different_return_selector",
                    obj["source"] == "step_output"
                    and obj["step_id"] == reader["id"]
                    and obj["selector"] == [case["selector"]],
                )
                field = next(
                    p
                    for s in views[0]["steps"]
                    if s["step_id"] == processor["id"]
                    for p in s["parameters"]
                    if p["name"] == case["option"]
                )
                if field["kind"] == "workflow_input":
                    option_ref = processor["arguments"][case["option"]]["name"]
                    edited = {
                        "input_values": {ref["name"]: "[3,5,7]", option_ref: 4}
                    }
                else:
                    edited = {
                        "input_values": {ref["name"]: "[3,5,7]"},
                        "step_changes": [
                            {
                                "step_id": processor["id"],
                                "parameter": case["option"],
                                "value": 4,
                            }
                        ],
                    }
                shown = (
                    next(
                        v
                        for v in views[0]["inputs"]
                        if v["name"] == field["input_name"]
                    )
                    if field["kind"] == "workflow_input"
                    else field
                )
                check(
                    "configured_value_appears_for_user",
                    shown.get("value") == 3,
                )
                boundary = state["__interrupt__"][0]
                command = {
                    "resume": {
                        "action": "approve_plan",
                        "plan_id": views[0]["plan_id"],
                        "plan_revision": views[0]["plan_revision"],
                        **edited,
                    }
                }
                identity = resume_identity(str(uuid4()), boundary.id, command)
                state = await graph.ainvoke(
                    Command(
                        resume={
                            boundary.id: resume_envelope(identity, command)
                        }
                    ),
                    config,
                    durability="sync",
                )
                item["approved_snapshot"] = state.get("approved_snapshot")
                check(
                    "hitl_approval_completed_without_executor",
                    state["final_response"]["status"] == "plan_approved"
                    and not state.get("execution_id"),
                )
                check(
                    "user_input_change_frozen",
                    state["approved_snapshot"]["input_values"][ref["name"]]
                    == "[3,5,7]",
                )
                final = next(
                    s
                    for s in state["approved_snapshot"]["steps"]
                    if s["id"] == processor["id"]
                )["arguments"][case["option"]]
                final_value = (
                    state["approved_snapshot"]["input_values"][final["name"]]
                    if final["source"] == "workflow_input"
                    else final.get("value")
                )
                check("user_parameter_change_frozen", final_value == 4)
            except Exception as error:
                item["error_type"] = type(error).__name__
                item["error"] = str(error)[:3000]
                check("scenario_completed", False)
            finally:
                write_private_result(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-env", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with model_host_alias():
        report = asyncio.run(
            verify(load_model_env(args.model_env), args.output)
        )
    if any(
        not check["passed"]
        for item in report["cases"]
        for check in item["checks"]
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
