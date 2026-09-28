"""Workflow completion and generation nodes with YAML serialization."""

from __future__ import annotations

from copy import deepcopy

from pydantic import ValidationError

from agent_config import AgentSettings
from app.graphs.message_utils import as_message_content
from app.agents.orchestration.agent_interfaces import invoke_typed
from app.agents.orchestration.dependencies import AgentDependencies
from app.agents.workflow_generator.data_load_steps import (
    merge_required_data_load_steps,
    required_data_load_steps,
    validate_target_data_role_lineage,
)
from app.agents.workflow_generator.workflow_compiler import compile_workflow_plan
from app.agents.workflow_generator.workflow_generator_tools import (
    normalize_skill_condition_contract,
    read_skill_documents,
    validate_skill_names,
    validate_skill_condition_contract,
)
from app.graphs.state.analysis_workflow_state import AnalysisWorkflowState
from app.services.demo_artifact_store import build_workflow_output_dir
from app.services.workflow_persistence import NullWorkflowStore, WorkflowStore
from app.schemas.agents.workflow_generator_schema import (
    SkillSelectionOutput,
    WorkflowGenerationRequest,
)
from app.schemas.workflows.workflow_plan_format import WorkflowPlanOutput


MAX_WORKFLOW_GENERATION_ATTEMPTS = 3


def _validation_feedback(error: Exception) -> str:
    return (
        "Previous WorkflowPlanOutput failed schema validation or deterministic "
        "compilation. Regenerate the complete WorkflowPlanOutput while preserving "
        "the user goal and confirmed inputs. Do not return a patch. "
        f"Validation error: {error}"
    )


def _has_answer(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True


def _apply_confirmed_additional_information(
    document: dict,
    additional_information: dict,
) -> dict:
    workflow = document["workflow"]
    input_schema = workflow.setdefault("input_schema", {})
    inputs = workflow.setdefault("inputs", {})
    provenance = workflow.setdefault("input_provenance", {})
    unresolved = workflow.setdefault("unresolved_inputs", [])
    confirmed_names = set()

    for name, value in additional_information.items():
        if name not in input_schema or not _has_answer(value):
            continue
        inputs[name] = value
        provenance[name] = {
            "source": "user_answer",
            "confirmed": True,
        }
        confirmed_names.add(name)

    if confirmed_names:
        workflow["unresolved_inputs"] = [
            item
            for item in unresolved
            if item.get("name") not in confirmed_names
        ]

    remaining_unresolved = workflow.get("unresolved_inputs", [])
    if workflow.get("status") == "needs_input" and not remaining_unresolved:
        all_required_confirmed = all(
            (
                not definition.get("required", True)
                or (
                    _has_answer(inputs.get(name))
                    and provenance.get(name, {}).get("confirmed")
                )
            )
            for name, definition in input_schema.items()
        )
        if all_required_confirmed:
            workflow["status"] = "ready"

    return document


def _require_human_confirmation_for_inputs(
    document: dict,
    confirmed_information: dict,
) -> dict:
    """Discard planner-filled values for inputs that require human confirmation."""
    workflow = document["workflow"]
    input_schema = workflow.setdefault("input_schema", {})
    inputs = workflow.setdefault("inputs", {})
    provenance = workflow.setdefault("input_provenance", {})
    unresolved = workflow.setdefault("unresolved_inputs", [])
    unresolved_by_name = {
        item.get("name"): item
        for item in unresolved
        if isinstance(item, dict) and item.get("name")
    }

    for name, definition in input_schema.items():
        if (
            not definition.get("required", True)
            or definition.get("allow_llm_inference", False)
            or _has_answer(confirmed_information.get(name))
        ):
            continue
        inputs[name] = None
        provenance[name] = {"source": None, "confirmed": False}
        if name not in unresolved_by_name:
            unresolved.append(
                {
                    "name": name,
                    "question": f"{name} 값을 입력해주세요.",
                    "required_for": [],
                }
            )

    if unresolved:
        workflow["status"] = "needs_input"
    return document


def _validate_data_load_contract(
    document: dict, selection: dict, required_steps: list[dict]
) -> None:
    steps = document["workflow"]["steps"]
    expected_ids = {step["id"] for step in required_steps}
    load_steps = [
        step
        for step in steps
        if step.get("skill") == "data_load"
        and step.get("id") in expected_ids
    ]
    expected_count = selection["data_count"]
    if len(load_steps) != expected_count:
        raise ValueError(
            "workflow must contain exactly one data_load step per selected dataset "
            f"(expected={expected_count}, actual={len(load_steps)})"
        )
    actual_ids = {step["id"] for step in load_steps}
    if actual_ids != expected_ids:
        raise ValueError(
            "workflow data_load step IDs must match required_data_load_steps"
        )


def apply_missing_information_to_workflow(
    state: AnalysisWorkflowState,
) -> dict:
    document = _apply_confirmed_additional_information(
        deepcopy(state["workflow"]),
        state.get("additional_information", {}),
    )
    return {
        "workflow": document,
        "workflow_status": document["workflow"]["status"],
        "approval": {},
        "messages": [
            {
                "role": "assistant",
                "name": "workflow_input_resolver",
                "content": as_message_content(
                    {
                        "message": "Workflow input answers were applied.",
                        "status": document["workflow"]["status"],
                        "unresolved_inputs": document["workflow"].get(
                            "unresolved_inputs", []
                        ),
                    }
                ),
            }
        ],
    }


def _add_current_workflow_candidate(
    state: AnalysisWorkflowState, store: WorkflowStore
) -> dict:
    candidates = deepcopy(state.get("workflow_candidates", []))
    origin = state["workflow_origin"]
    origin_count = sum(
        1 for candidate in candidates if candidate.get("origin") == origin
    )
    workflow = deepcopy(state["workflow"])
    intent = state.get("analysis_intent", {}).get("intent", "unknown")
    catalog_id = store.save_catalog_workflow(
        session_id=state["session_id"],
        task_id=state["task_id"],
        intent=str(intent),
        revision=int(state.get("workflow_revision", 1)),
        workflow=workflow,
    )
    candidate = {
        "id": f"{origin}_{origin_count + 1}",
        "origin": origin,
        "status": workflow["workflow"]["status"],
        "workflow": workflow,
        "catalog_id": catalog_id or None,
    }
    candidates.append(candidate)
    return {
        "workflow_candidates": candidates,
        "messages": [
            {
                "role": "assistant",
                "name": "workflow_candidate_collector",
                "content": as_message_content(
                    {
                        "status": "candidate_added",
                        "candidate_id": candidate["id"],
                        "origin": origin,
                        "workflow_status": candidate["status"],
                    }
                ),
            }
        ],
    }


def add_current_workflow_candidate(state: AnalysisWorkflowState) -> dict:
    """Compatibility node for callers that do not configure durable storage."""

    return _add_current_workflow_candidate(state, NullWorkflowStore())


def make_add_current_workflow_candidate(store: WorkflowStore):
    def add_candidate(state: AnalysisWorkflowState) -> dict:
        return _add_current_workflow_candidate(state, store)

    return add_candidate


def _make_workflow_node(
    deps: AgentDependencies,
    settings: AgentSettings,
    *,
    generation_mode: str,
    workflow_origin: str,
):
    def workflow_node(state: AnalysisWorkflowState) -> dict:
        revision = state.get("workflow_revision", 0) + 1
        if revision > settings.max_workflow_revisions:
            raise RuntimeError("maximum workflow revision count exceeded")

        required_steps = required_data_load_steps(
            state["data_selection"],
            data_mock=settings.data_mock,
        )
        artifact_output_dir = build_workflow_output_dir(settings, state)
        workflow_context = {"output_dir": artifact_output_dir}
        request = WorkflowGenerationRequest(
            user_request=state["user_request"],
            analysis_intent=state["analysis_intent"]["intent"],
            analysis_context=state["analysis_context"],
            workflow_context=workflow_context,
            data_selection=state["data_selection"],
            generation_mode=generation_mode,
            additional_information=state.get("additional_information", {}),
            rejection_feedback=state.get("approval_feedback"),
            previous_workflow=(
                state.get("workflow")
                if state.get("routing_context") == "workflow_rejected"
                else None
            ),
        )

        selected_skill_names: list[str] | None = None
        workflow_resources: dict | None = None
        if deps.skill_selector_agent is not None:
            selector_payload = {
                "user_request": request.user_request,
                "analysis_intent": request.analysis_intent,
                "analysis_context": request.analysis_context.model_dump(
                    mode="json"
                ),
                "data_selection": request.data_selection.model_dump(
                    mode="json"
                ),
                "generation_mode": request.generation_mode,
                "additional_information": request.additional_information,
                "rejection_feedback": request.rejection_feedback,
            }
            selection = invoke_typed(
                deps.skill_selector_agent,
                selector_payload,
                SkillSelectionOutput,
            )
            selected_skill_names = validate_skill_names(selection.skill_names)
            selected_skill_names = [
                name for name in selected_skill_names
                if name != "data_load"
            ]            
            selected_roles = {
                item["role"] for item in state["data_selection"]["datasets"]
            }
            if (
                selected_roles == {"x", "y"}
                and "dataset_preparation" not in selected_skill_names
            ):
                selected_skill_names.append("dataset_preparation")
                selected_skill_names = validate_skill_names(
                    selected_skill_names
                )
            # 상세 문서 조회 전에 최종 Skill 목록을 확인한다.
            if not selected_skill_names:
                raise ValueError("분석에 사용할 Skill이 선택되지 않았습니다.")    
                        
            workflow_resources = read_skill_documents.invoke(
                {"skill_names": selected_skill_names}
            )

        last_error: Exception | None = None
        request_payload = request
        for _ in range(MAX_WORKFLOW_GENERATION_ATTEMPTS):
            failed_plan: dict | None = None
            try:
                generation_payload = request_payload.model_dump(mode="json")
                generation_payload["data_role_bindings"] = [
                    {
                        "dataset_id": f"{dataset['data_type']}-{index}",
                        "role": dataset["role"],
                        "load_step_id": f"load_data_{index}",
                        "data_output": f"steps.load_data_{index}.outputs.data",
                    }
                    for index, dataset in enumerate(
                        state["data_selection"]["datasets"], start=1
                    )
                ]
                if workflow_resources is not None:
                    generation_payload.update(
                        {
                            "selected_skill_names": selected_skill_names,
                            "workflow_resources": workflow_resources,
                        }
                    )
                plan_output = invoke_typed(
                    deps.workflow_agent,
                    generation_payload,
                    WorkflowPlanOutput,
                )
                failed_plan = plan_output.model_dump(mode="json")
                failed_plan["workflow"]["context"] = {
                    **failed_plan["workflow"].get("context", {}),
                    **workflow_context,
                }
                generated_document = compile_workflow_plan(
                    failed_plan,
                    external_step_outputs={
                        (f"load_data_{index}", "data")
                        for index in range(
                            1,
                            state["data_selection"]["data_count"] + 1,
                        )
                    },
                )
                generated_document["workflow"]["context"] = {
                    **generated_document["workflow"].get("context", {}),
                    **workflow_context,
                }
                generated_document = _require_human_confirmation_for_inputs(
                    generated_document,
                    state.get("additional_information", {}),
                )
                generated_document = _apply_confirmed_additional_information(
                    generated_document,
                    state.get("additional_information", {}),
                )
                document = merge_required_data_load_steps(
                    generated_document,
                    state["data_selection"],
                    data_mock=settings.data_mock,
                )
                normalize_skill_condition_contract(document)
                validate_skill_condition_contract(document)
                _validate_data_load_contract(
                    document, state["data_selection"], required_steps
                )
                validate_target_data_role_lineage(
                    document,
                    state["data_selection"],
                )
                break
            except (ValidationError, ValueError) as exc:
                last_error = exc
                request_payload = request.model_copy(
                    update={
                        "validation_feedback": _validation_feedback(exc),
                        "previous_workflow": (
                            failed_plan
                            or request_payload.previous_workflow
                        ),
                    }
                )
        else:
            raise RuntimeError(
                "workflow generation failed validation after retries. "
                f"Last validation error: {last_error}"
            ) from last_error

        # TODO(CRUD): 에이전트 메시지 POST
        # TODO(CRUD): workflow JSON POST
        return {
            "workflow": document,
            "workflow_status": document["workflow"]["status"],
            "workflow_origin": workflow_origin,
            "workflow_revision": revision,
            "artifact_output_dir": artifact_output_dir,
            "approval": {},
            "messages": [
                {
                    "role": "assistant",
                    "name": "workflow_generator",
                    "content": as_message_content(
                        {
                            "message": "Workflow가 생성되었습니다.",
                            "mode": generation_mode,
                            "revision": revision,
                            "status": document["workflow"]["status"],
                        }
                    ),
                }
            ],
        }

    return workflow_node


def make_generate_workflow(
    deps: AgentDependencies,
    settings: AgentSettings,
):
    return _make_workflow_node(
        deps,
        settings,
        generation_mode="new",
        workflow_origin="generated",
    )
