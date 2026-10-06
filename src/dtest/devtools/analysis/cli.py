"""Offline mock graph console. Actual service integration uses the Runs API."""
from __future__ import annotations
import argparse
import asyncio
import json
from typing import Sequence
from langgraph.types import Command
from dtest.devtools.analysis.runtime import local_runtime, local_input, compiled_in_memory_graph


def public_result(state):
    """Keep private approved Tool source/checkpoint details off the console."""
    return {"events": [item["envelope"] for item in state.get("public_events", [])],
            "interaction": state.get("interaction_data"),
            "result": state.get("final_response")}


async def run(request, *, interactive=False):
    runtime = local_runtime()
    graph = compiled_in_memory_graph(runtime)
    value = local_input(runtime, request)
    config = {"configurable": {"thread_id": value["session_id"]}}
    state = await graph.ainvoke(value, config, durability="sync")
    while True:
        print(json.dumps(public_result(state), ensure_ascii=False, indent=2))
        if not interactive or not state.get("__interrupt__"):
            return state
        raw = input("HITL action JSON (종료: quit): ").strip()
        if raw.lower() in {"quit", "exit"}:
            return state
        try:
            action = json.loads(raw)
            if not isinstance(action, dict):
                raise ValueError("action must be a JSON object")
        except (ValueError, json.JSONDecodeError) as exc:
            print(str(exc))
            continue
        command = {"resume": action}
        from dtest.contracts.user_resume import resume_identity, resume_envelope
        from uuid import uuid4
        target = state["__interrupt__"][0].id
        identity = resume_identity(str(uuid4()), target, command)
        state = await graph.ainvoke(Command(resume={target: resume_envelope(identity, command)}),
                                   config, durability="sync")


def main(argv: Sequence[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", default="데이터의 품질과 이상치를 분석할 계획을 제안해줘")
    parser.add_argument("--interactive", action="store_true", help="현재 typed HITL action JSON으로 편집·승인")
    args = parser.parse_args(argv)
    asyncio.run(run(args.request, interactive=args.interactive))


if __name__ == "__main__":
    main()
