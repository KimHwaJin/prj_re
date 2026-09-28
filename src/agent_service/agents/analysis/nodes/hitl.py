"""Dynamic HITL interrupt nodes for selection, questions, and review."""

from __future__ import annotations

from copy import deepcopy

from agent_config import (
    AgentSettings,
    TEST_DATA_SELECTION,
    TEST_DATA_SELECTION_TRIGGER,
)
from agent_service.agents.analysis.hitl_protocol import request_human_input
from agent_service.agents.analysis.message_utils import as_message_content
from agent_service.agents.analysis.state import AnalysisWorkflowState
from agent_service.agents.analysis.artifacts import (
    build_workflow_candidate_artifact_path,
    write_demo_json,
)
from agent_service.agents.analysis.schemas.agents.orchestration_schema import (
    AdditionalInformationResponse,
    AnalysisContextResponse,
    ApprovalDecision,
    DataSelectionResponse,
    NextUserRequestResponse,
    WorkflowCandidateSelectionResponse,
)


def _coerce_answer(value, definition: dict):
    input_type = str(definition.get("type", "")).lower()
    if not (
        input_type == "list"
        or input_type.startswith("list[")
        or input_type == "array"
    ):
        return value
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            import json

            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                return parsed
        return [item.strip() for item in stripped.split(",") if item.strip()]
    return value


def _coerce_answers_by_input_schema(
    answers: dict,
    input_schema: dict,
) -> dict:
    return {
        name: _coerce_answer(value, input_schema.get(name, {}))
        for name, value in answers.items()
    }


def wait_for_data_selection(state: AnalysisWorkflowState) -> dict:
    description = "분석에 사용할 X/Y 데이터를 선택해주세요."
    response = request_human_input(
        action_name="data_selection",
        description=description,
        args=deepcopy(TEST_DATA_SELECTION),
        allowed_decisions=["approve", "edit"],
        args_schema=DataSelectionResponse.model_json_schema(),
    )
    if (
        isinstance(response, str)
        and response.strip().lower() == TEST_DATA_SELECTION_TRIGGER
    ):
        response = deepcopy(TEST_DATA_SELECTION)
    selection = DataSelectionResponse.model_validate(response)
    # TODO(CRUD): 사용자 메시지 POST
    return {
        "data_selection": selection.model_dump(mode="json"),
    }


def collect_analysis_context(state: AnalysisWorkflowState) -> dict:
    description = "선택한 데이터로 무엇을 분석하거나 확인하고 싶으신가요?"
    response = request_human_input(
        action_name="analysis_context",
        description=description,
        args={"objective": "EDA"},
        allowed_decisions=["approve", "edit"],
        args_schema=AnalysisContextResponse.model_json_schema(),
    )

    if isinstance(response, str):
        response = {"objective": response}
    context = AnalysisContextResponse.model_validate(response)
    payload = context.model_dump(mode="json")

    # TODO(CRUD): 사용자 메시지 POST
    return {
        "analysis_context": payload,
    }


def collect_missing_information(state: AnalysisWorkflowState) -> dict:
    workflow = state["workflow"]["workflow"]
    input_schema = workflow.get("input_schema", {})
    unresolved = [
        {
            **item,
            "input_schema": input_schema.get(item.get("name"), {}),
        }
        for item in workflow.get("unresolved_inputs", [])
    ]
    description = "Workflow 완성에 필요한 정보를 입력해주세요."
    form_args = {}
    form_properties = {}
    required_fields = []
    for item in unresolved:
        name = item.get("name")
        if not name:
            continue
        definition = item.get("input_schema") or {}
        input_type = str(definition.get("type") or "str").lower()
        if input_type in {"int", "integer"}:
            json_type, default = "integer", 0
        elif input_type in {"float", "number"}:
            json_type, default = "number", 0
        elif input_type in {"bool", "boolean"}:
            json_type, default = "boolean", False
        elif input_type == "list" or input_type.startswith("list["):
            json_type, default = "array", []
        else:
            json_type, default = "string", ""

        form_args[name] = definition.get("default", default)
        field_schema = {
            "type": json_type,
            "title": name,
            "description": item.get("question") or name,
        }
        if json_type == "string" and definition.get("required", True):
            field_schema["minLength"] = 1
        if json_type == "array":
            field_schema["items"] = {}
        form_properties[name] = field_schema
        if definition.get("required", True):
            required_fields.append(name)

    form_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": form_properties,
        "required": required_fields,
    }
    response = request_human_input(
        action_name="additional_information",
        description=description,
        args=form_args,
        allowed_decisions=["approve", "edit"],
        args_schema=form_schema,
    )
    if isinstance(response, dict) and "answers" not in response:
        response = {"answers": response}
    answers = AdditionalInformationResponse.model_validate(response)
    # TODO(CRUD): 사용자 메시지 POST
    coerced_answers = _coerce_answers_by_input_schema(
        answers.answers,
        input_schema,
    )
    merged = {**state.get("additional_information", {}), **coerced_answers}
    return {
        "additional_information": merged,
    }


def _workflow_candidate_description(
    candidates: list[dict],
    intro: str,
) -> str:
    """Build a compact, CLI-level Markdown summary for candidate review."""
    sections = [intro]
    for index, candidate in enumerate(candidates, start=1):
        workflow = candidate.get("workflow", {}).get("workflow", {})
        name = workflow.get("name") or candidate.get("id")
        status = workflow.get("status") or candidate.get("status") or "unknown"
        origin = candidate.get("origin") or "unknown"
        execution_mode = workflow.get("execution_mode") or "static"
        lines = [
            f"### {index}. {name}",
            f"- ID: `{candidate.get('id')}`",
            f"- 상태: `{status}` / 출처: `{origin}` / 실행 모드: `{execution_mode}`",
        ]
        if workflow.get("goal"):
            lines.append(f"- 목표: {workflow['goal']}")
        unresolved = workflow.get("unresolved_inputs") or []
        unresolved_names = [
            item.get("name")
            for item in unresolved
            if isinstance(item, dict) and item.get("name")
        ]
        if unresolved_names:
            lines.append(f"- 필요 입력: {', '.join(unresolved_names)}")

        steps = workflow.get("steps") or []
        if steps:
            lines.append("- Steps:")
        for step in steps:
            dependencies = step.get("depends_on") or []
            dependency_text = ", ".join(dependencies) if dependencies else "없음"
            step_execution = step.get("execution") or "always"
            lines.append(
                f"  - {step.get('order')}. `{step.get('skill')}` "
                f"(`{step.get('id')}`) — {step_execution}, "
                f"depends_on: {dependency_text}"
            )
            for tool in step.get("tools") or []:
                tool_execution = tool.get("execution") or "always"
                condition = tool.get("condition")
                condition_text = ""
                if tool_execution == "conditional" and isinstance(condition, dict):
                    decision_id = condition.get("decision_id")
                    condition_text = (
                        f", decision_id: {decision_id}" if decision_id else ""
                    )
                lines.append(
                    f"    - `{tool.get('tool')}` [{tool_execution}{condition_text}]"
                )
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def make_select_workflow_candidate(settings: AgentSettings):
    def select_workflow_candidate(state: AnalysisWorkflowState) -> dict:
        candidates = state.get("workflow_candidates", [])
        description = "Workflow 후보를 선택하거나 피드백과 함께 거절해주세요."
        description = _workflow_candidate_description(candidates, description)

        selection_args = {
            "candidate_number": 1,
            "workflow_overview": description,
        }
        selection_schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "candidate_number": {
                    "type": "integer",
                    "title": "Candidate Number",
                    "minimum": 1,
                    "maximum": max(1, len(candidates)),
                },
                "workflow_overview": {
                    "type": "string",
                    "title": "Workflow Overview",
                    "description": "검토용 요약입니다. 후보 선택은 Candidate Number로 변경하세요.",
                    "readOnly": True,
                },
            },
            "required": ["candidate_number", "workflow_overview"],
        }
        response = request_human_input(
            action_name="workflow_candidate_selection",
            description=description,
            args=selection_args,
            allowed_decisions=["approve", "edit", "reject"],
            args_schema=selection_schema,
        )
        if isinstance(response, str):
            stripped = response.strip()
            if stripped.isdigit():
                candidate_index = int(stripped) - 1
                if 0 <= candidate_index < len(candidates):
                    stripped = str(candidates[candidate_index].get("id") or "")
            response = {"selected_candidate_id": stripped}
        if isinstance(response, bool):
            response = {"approved": response}
        if isinstance(response, dict) and "candidate_number" in response:
            candidate_index = int(response["candidate_number"]) - 1
            if not 0 <= candidate_index < len(candidates):
                raise ValueError(
                    f"candidate_number must be between 1 and {len(candidates)}"
                )
            response = {
                "approved": True,
                "selected_candidate_id": candidates[candidate_index].get("id"),
                "feedback": None,
            }
        selection = WorkflowCandidateSelectionResponse.model_validate(response)
        payload = selection.model_dump(mode="json")
        if not selection.approved:
            return {
                "workflow_candidate_selection": payload,
                "approval_feedback": selection.feedback,
                "user_request": selection.feedback.strip(),
                "routing_context": "workflow_rejected",
            }

        selected = next(
            (
                candidate
                for candidate in candidates
                if candidate.get("id") == selection.selected_candidate_id
            ),
            None,
        )
        if selected is None:
            raise ValueError(
                f"unknown workflow candidate: {selection.selected_candidate_id!r}"
            )
        workflow = deepcopy(selected["workflow"])
        artifact_output_dir = state.get("artifact_output_dir")
        if artifact_output_dir:
            workflow["workflow"]["context"] = {
                **workflow["workflow"].get("context", {}),
                "output_dir": artifact_output_dir,
            }

        artifact_files = dict(state.get("artifact_files", {}))
        workflow_status = workflow["workflow"]["status"]
        if settings.demo_artifacts_enabled:
            workflow_id = str(workflow["workflow"].get("id") or selected["id"])
            path = build_workflow_candidate_artifact_path(
                settings,
                session_id=state["session_id"],
                task_id=state["task_id"],
                workflow_id=workflow_id,
            )
            artifact_files["selected_workflow_candidate"] = str(
                write_demo_json(path, workflow)
            )

        return {
            "workflow_candidate_selection": payload,
            "workflow": workflow,
            "workflow_catalog_id": selected.get("catalog_id"),
            "workflow_status": workflow_status,
            "workflow_origin": selected["origin"],
            "approval": {},
            "approval_feedback": None,
            "artifact_files": artifact_files,
        }

    return select_workflow_candidate

def review_workflow(state: AnalysisWorkflowState) -> dict:
    intro = "완성된 Workflow를 승인하거나 피드백과 함께 거절해주세요."
    description = _workflow_candidate_description(
        [
            {
                "id": state["workflow"]["workflow"].get("id", "workflow"),
                "origin": state["workflow_origin"],
                "status": state["workflow"]["workflow"].get("status"),
                "workflow": state["workflow"],
            }
        ],
        intro,
    )
    review_args = {
        "kind": "workflow_approval",
        "workflow_origin": state["workflow_origin"],
        "workflow_overview": description,
    }
    review_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "kind": {
                "type": "string",
                "title": "Kind",
                "readOnly": True,
            },
            "workflow_origin": {
                "type": "string",
                "title": "Workflow Origin",
                "readOnly": True,
            },
            "workflow_overview": {
                "type": "string",
                "title": "Workflow Overview",
                "description": "승인 검토용 요약입니다. 전체 실행 정의는 graph state에 보관됩니다.",
                "readOnly": True,
            },
        },
        "required": ["kind", "workflow_origin", "workflow_overview"],
    }
    response = request_human_input(
        action_name="workflow_approval",
        description=intro,
        args=review_args,
        allowed_decisions=["approve", "reject"],
        args_schema=review_schema,
    )
    if isinstance(response, bool):
        response = {"approved": response}
    if isinstance(response, dict) and "workflow_overview" in response:
        response = {"approved": True, "feedback": None}
    decision = ApprovalDecision.model_validate(response)
    payload = decision.model_dump(mode="json")
    # TODO(CRUD): 사용자 메시지 POST
    # TODO(CRUD): 승인/거절 결과 PATCH
    updates = {
        "approval": payload,
        "approval_feedback": None if decision.approved else decision.feedback,
        "messages": [
            {
                "role": "user",
                "name": "workflow_approval",
                "content": as_message_content(payload),
            }
        ],
    }
    if not decision.approved:
        updates.update(
            {
                "user_request": decision.feedback.strip(),
                "routing_context": "workflow_rejected",
            }
        )
    return updates


def await_next_user_request(state: AnalysisWorkflowState) -> dict:
    description = "추가 요청을 입력하거나 대화를 종료해주세요."
    response = request_human_input(
        action_name="next_user_request",
        description=description,
        args={
            "kind": "next_user_request",
            "message": description,
            "options": ["continue", "cancel"],
            "schema": NextUserRequestResponse.model_json_schema(),
        },
    )
    decision = NextUserRequestResponse.model_validate(response)
    payload = decision.model_dump(mode="json")
    update: dict = {
        "conversation_control": payload,
        "return_to": "main_conversation",
        "action_query": None,
    }
    if decision.action == "continue":
        request = decision.user_request.strip()
        update.update(
            {
                "user_request": request,
                "top_intent": {},
                "analysis_intent": {},
                "data_selection": {},
                "analysis_context": {},
                "artifact_output_dir": "",
                "artifact_files": {},
                "recommendation": {},
                "workflow_candidates": [],
                "workflow_candidate_selection": {},
                "workflow_origin": "",
                "additional_information": {},
                "workflow": {},
                "workflow_catalog_id": None,
                "workflow_status": "",
                "workflow_revision": 0,
                "approval": {},
                "approval_feedback": None,
                "notebook": {},
                "adaptive_pending_tool_id": None,
                "adaptive_generated_tool_ids": [],
                "adaptive_executed_tool_ids": [],
                "adaptive_observations": {},
                "adaptive_runtime_decisions": {},
                "adaptive_execution_plan": {},
                "adaptive_decision_history": [],
                "adaptive_round": 0,
                "adaptive_status": "",
                "execution_mode": "",
                "idempotency_key": "",
                "execution_id": "",
                "executor_operation_id": "",
                "executor_operation_steps": [],
                "executor_state_version": 0,
                "executor_next_sequence": 0,
                "executor_operation_number": 0,
                "executor_requires_state_version": False,
                "executor_wait_phase": "execution_completed",
                "executor_tool_results": [],
                "executor_result_history": [],
                "executor_request_path": "",
                "execution_steps": [],
                "execution_events": [],
                "step_results": [],
                "executor_submit_response": {},
                "execution_status": "",
                "analysis_report": {},
                "report_status": "",
                "report_artifact_request": {},
                "report_artifact_response": {},
                "final_response": {},
                "messages": [{"role": "user", "content": request}],
            }
        )
        # TODO(CRUD): 사용자 메시지 POST
    return update
