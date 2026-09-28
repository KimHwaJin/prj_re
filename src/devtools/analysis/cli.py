"""Interactive CLI for manually testing the user-agent LangGraph."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Sequence

from langgraph.types import Command

from agent_config import build_local_mock_request_context, load_agent_settings
from agent_service.agents.analysis.dependencies import create_llm_dependencies
from devtools.analysis.checkpoint import compiled_in_memory_graph, compiled_postgres_graph
from agent_service.agents.analysis.hitl_protocol import hitl_request_args
from app.observability import setup_phoenix, shutdown_phoenix


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _print_new_agent_messages(result: dict[str, Any], displayed: int) -> int:
    messages = result.get("messages", [])
    for message in messages[displayed:]:
        if message.get("role") not in {"agent", "assistant"}:
            continue
        name = message.get("name", "agent")
        content = message.get("content")
        print(f"\n[{name}]")
        if isinstance(content, str):
            print(content)
        else:
            _print_json(content)
    return len(messages)


async def _stream_graph(
    graph: Any,
    graph_input: Any,
    *,
    config: dict[str, Any],
    displayed_messages: int,
) -> tuple[dict[str, Any], int]:
    """Run until completion/interrupt while printing each node's messages."""
    latest_state: dict[str, Any] = {}
    async for state in graph.astream(
        graph_input,
        config=config,
        stream_mode="values",
    ):
        latest_state = state
        displayed_messages = _print_new_agent_messages(
            latest_state,
            displayed_messages,
        )
    return latest_state, displayed_messages


async def _existing_message_count(graph: Any, config: dict[str, Any]) -> int:
    try:
        snapshot = await graph.aget_state(config)
    except Exception:
        return 0
    values = getattr(snapshot, "values", None) or {}
    messages = values.get("messages", []) if isinstance(values, dict) else []
    return len(messages) if isinstance(messages, list) else 0


def _prompt_json() -> Any:
    while True:
        raw = input("JSON을 한 줄로 입력하세요: ").strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            print(f"올바른 JSON이 아닙니다: {exc}")


def _hitl_cli_payload(request: Any) -> dict[str, Any]:
    """Return HITL args with a CLI-friendly kind filled from the action name."""
    action_name = None
    description = None

    if isinstance(request, dict):
        action_requests = request.get("action_requests")
        if isinstance(action_requests, list) and len(action_requests) == 1:
            action = action_requests[0]
            if isinstance(action, dict):
                action_name = action.get("name")
                description = action.get("description")
    elif isinstance(request, list) and len(request) == 1 and isinstance(request[0], dict):
        action = request[0].get("action_request")
        if isinstance(action, dict):
            action_name = action.get("name")
            description = action.get("description")

    payload = hitl_request_args(request)
    if payload.get("kind") is None and isinstance(action_name, str):
        payload = {**payload, "kind": action_name}
    if payload.get("message") is None and isinstance(description, str):
        payload = {**payload, "message": description}
    return payload


def _prompt_additional_information(payload: dict[str, Any]) -> dict[str, Any]:
    answers: dict[str, Any] = {}
    questions = payload.get("questions") or []
    for index, question in enumerate(questions, start=1):
        if not isinstance(question, dict):
            answers[f"answer_{index}"] = input(f"{question}: ").strip()
            continue
        name = question.get("name") or f"answer_{index}"
        prompt = question.get("question") or name
        answers[name] = input(f"{prompt}: ").strip()
    if not answers:
        value = _prompt_json()
        if isinstance(value, dict) and "answers" in value:
            return value
        if not isinstance(value, dict):
            raise ValueError("추가 정보는 JSON object여야 합니다.")
        answers = value
    return {"answers": answers}


def _print_workflow_candidates(payload: dict[str, Any]) -> None:
    candidates = payload.get("candidates") or []
    print("\n[Workflow 후보]")
    if not candidates:
        print("후보가 없습니다.")
        return
    for index, candidate in enumerate(candidates, start=1):
        document = candidate.get("workflow") or {}
        workflow = document.get("workflow") if isinstance(document, dict) else {}
        if not isinstance(workflow, dict):
            workflow = {}
        label = workflow.get("name") or candidate.get("id")
        status = workflow.get("status") or "unknown"
        origin = candidate.get("origin") or "unknown"
        execution_mode = workflow.get("execution_mode") or "static"
        print(f"\n  {index}. {label} [{status}, {origin}, {execution_mode}]")
        print(f"     id: {candidate.get('id')}")
        goal = workflow.get("goal")
        if goal:
            print(f"     목표: {goal}")
        unresolved = workflow.get("unresolved_inputs") or []
        if unresolved:
            names = [item.get("name") for item in unresolved if item.get("name")]
            if names:
                print(f"     필요 입력: {', '.join(names)}")
        steps = workflow.get("steps") or []
        if steps:
            print("     Steps:")
        for step in steps:
            print(
                f"       {step.get('order')}. {step.get('skill')} "
                f"({step.get('id')})"
            )
            for tool in step.get("tools", []):
                execution = tool.get("execution") or "always"
                print(f"          - {tool.get('tool')} [{execution}]")

def _print_workflow_summary(payload: dict[str, Any]) -> None:
    document = payload.get("workflow") or {}
    workflow = document.get("workflow") if isinstance(document, dict) else None
    if not isinstance(workflow, dict):
        return
    print("\n[완성된 Workflow]")
    print(f"이름: {workflow.get('name')}")
    print(f"목표: {workflow.get('goal')}")
    for step in workflow.get("steps", []):
        print(
            f"  {step.get('order')}. {step.get('skill')} "
            f"({step.get('id')})"
        )
        for tool in step.get("tools", []):
            print(f"     - {tool.get('tool')}")


def _prompt_resume_value(payload: dict[str, Any]) -> Any:
    kind = payload.get("kind")
    message = payload.get("message")
    if message:
        print(f"\n{message}")

    if kind == "data_selection":
        raw = input("데이터 선택: ").strip()
        if raw.lower() == "mock":
            return "mock"
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            print("여러 필드 입력이 필요하므로 mock 또는 JSON을 사용해주세요.")
            return _prompt_json()

    if kind == "analysis_context":
        return {"objective": input("분석 목표: ").strip()}

    if kind == "additional_information":
        return _prompt_additional_information(payload)

    if kind == "workflow_candidate_selection":
        _print_workflow_candidates(payload)
        candidates = payload.get("candidates") or []
        raw = input("선택할 후보 번호 또는 id: ").strip()
        if raw.isdigit() and not candidates:
            return {"candidate_number": int(raw)}
        if raw.isdigit():
            index = int(raw) - 1
            if 0 <= index < len(candidates):
                return {"selected_candidate_id": candidates[index].get("id")}
        return {"selected_candidate_id": raw}
    if kind == "workflow_approval":
        _print_workflow_summary(payload)
        approved = input("승인하시겠습니까? [y/n]: ").strip().lower()
        if approved in {"y", "yes", "예", "네"}:
            return {"approved": True}
        feedback = input("거절 사유: ").strip()
        return {"approved": False, "feedback": feedback}

    if kind == "adaptive_execution_results":
        pending = payload.get("pending_conditional_tool") or {}
        required_tool_id = payload.get("required_condition_tool_id")
        print("\n[Adaptive 실행 결과 입력]")
        print(f"판단 대상: {pending.get('tool_id')}")
        print(f"필요한 condition Tool 결과: {required_tool_id}")
        cell_paths = payload.get("current_cell_paths") or []
        if cell_paths:
            print("실행할 셀 파일:")
            for path in cell_paths:
                print(f"  - {path}")
        print(
            "입력 예: "
            f'{{"results":[{{"tool_id":"{required_tool_id}",'
            '"result":<실행 결과>}]}}'
        )
        return _prompt_json()

    if kind == "next_user_request":
        request = input("다음 요청: ").strip()
        if request.lower() in {"cancel", "quit", "exit", "종료", "취소"}:
            return {"action": "cancel"}
        return {"action": "continue", "user_request": request}

    print(f"알 수 없는 interrupt 종류입니다: {kind!r}")
    return _prompt_json()


async def _run_conversation(graph: Any, *, session_id: str) -> None:
    context = build_local_mock_request_context(session_id=session_id)
    config = {"configurable": {"thread_id": context["thread_id"]}}
    request = input("\n질문을 입력하세요: ").strip()
    if not request:
        raise ValueError("질문을 입력해야 합니다.")

    displayed_messages = await _existing_message_count(graph, config)
    result, displayed_messages = await _stream_graph(
        graph,
        {**context, "user_request": request},
        config=config,
        displayed_messages=displayed_messages,
    )

    while True:
        interrupts = result.get("__interrupt__") or []
        if not interrupts:
            break
        resume_value = _prompt_resume_value(
            _hitl_cli_payload(interrupts[0].value)
        )
        result, displayed_messages = await _stream_graph(
            graph,
            Command(resume=resume_value),
            config=config,
            displayed_messages=displayed_messages,
        )

    print("\n[최종 결과]")
    _print_json(result.get("final_response") or result)
    if result.get("artifact_files"):
        print("\n[저장된 demo artifact]")
        _print_json(result["artifact_files"])


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="사용자-Agent LangGraph 대화 흐름을 CLI에서 테스트합니다."
    )
    parser.add_argument(
        "--session-id",
        default="cli-session-001",
        help="테스트에 사용할 session_id",
    )
    parser.add_argument(
        "--postgres",
        action="store_true",
        help="InMemorySaver 대신 PostgreSQL checkpointer 사용",
    )
    args = parser.parse_args(argv)
    asyncio.run(_main(args))


async def _main(args) -> None:
    settings = load_agent_settings()
    setup_phoenix(settings)
    try:
        dependencies = create_llm_dependencies(settings)
        graph_context = (
            compiled_postgres_graph(dependencies, settings)
            if args.postgres
            else nullcontext(compiled_in_memory_graph(dependencies, settings))
        )
        async with graph_context as graph:
            await _run_conversation(graph, session_id=args.session_id)
    finally:
        shutdown_phoenix()


if __name__ == "__main__":
    main()


__all__ = ["main"]
