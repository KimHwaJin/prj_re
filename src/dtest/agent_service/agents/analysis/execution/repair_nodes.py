"""Checkpointed proposal → optional human approval → one correction Operation.

Model validation retries never submit code. The same accepted proposal/request is
reused after receipt replay; each accepted correction consumes one Run-wide attempt.
"""

from copy import deepcopy
from uuid import uuid4

from dtest.contracts.execution_repair import (
    RepairResponse,
    RepairInteractionData,
    validate_repair_action,
)
from dtest.contracts.plan_review import require
from dtest.contracts.events import ExecutorEvent
from dtest.agent_service.runtime.user_resume import (
    user_interrupt,
    record_user_resume,
)
from dtest.agent_service.runtime.executor_boundary import receipt_update
from dtest.agent_service.middleware.prompt_json import StructuredResponseError
from .repair_policy import (
    effective_snapshot,
    proposal_snapshot,
    verify_candidate,
    LEVELS,
)


def stop(state, reason):
    return {
        "execution_phase": "cancel",
        "analysis_failure": True,
        "repair_stop_reason": reason,
        "repair_candidate": None,
        "repair_review": None,
    }


class RepairNodes:
    def __init__(self, runtime, event_factory):
        self.runtime, self.event = runtime, event_factory

    async def propose(self, state):
        snapshot = effective_snapshot(state)
        if state.get("repair_attempts", 0) >= state["repair_max_attempts"]:
            return stop(state, "attempts_exhausted")
        if (
            state.get("executor_operation_number", 0)
            >= self.runtime.settings.agent_max_operations
        ):
            return stop(state, "operation_budget_exhausted")
        # No remote files/source are fetched by the model. All assets are the pinned deployment revision.
        context = {
            k: deepcopy(state[k])
            for k in (
                "approved_snapshot",
                "completed_steps",
                "skipped_steps",
                "execution_decisions",
                "observations",
                "failed_step_ids",
                "repair_max_attempts",
                "repair_history",
            )
        }
        context.update(
            execution_snapshot=state.get("execution_snapshot"),
            repair_attempts=state.get("repair_attempts", 0),
            repair_authorized_level=state.get(
                "repair_authorized_level",
                snapshot["execution"]["repair_level"],
            ),
        )
        payload = {
            "goal": snapshot["document"]["goal"],
            "repair_context": context,
            "levels": LEVELS,
            "service_level_limit": self.runtime.settings.agent_repair_level_limit,
            "available_skills": self.runtime.catalog.public_skills(),
            "active_tool_metadata": {
                tool: self.runtime.catalog.metadata["tools"].get(
                    source.get("origin_tool_id", tool),
                    {
                        "function_name": source["function_name"],
                        "description": (
                            "Execution-local function; see pinned source"
                        ),
                    },
                )
                for tool, source in snapshot["tool_sources"].items()
            },
            "skills": snapshot["skill_sources"],
            "operation_error": state.get("execution_error"),
        }
        try:
            response = await self.runtime.execution_role(
                "repair", state, payload
            )
            response = RepairResponse.model_validate(response)
            if not response.can_repair:
                return stop(state, "no_supported_repair")
            candidate = proposal_snapshot(
                state,
                response,
                self.runtime.catalog,
                level_limit=self.runtime.settings.agent_repair_level_limit,
            )
        except (
            StructuredResponseError,
            ValueError,
            TypeError,
            KeyError,
            SyntaxError,
        ) as exc:
            # Bad content is bounded by middleware. Transport and cancellation propagate normally.
            return {
                **stop(state, "invalid_repair_proposal"),
                "repair_validation_error": str(exc)[:4000],
            }
        events = [
            *state.get("public_events", []),
            self.event(
                state,
                "activity.updated",
                {
                    "kind": "repair",
                    "title": candidate["summary"],
                    "attempt": candidate["attempt"],
                    "max_attempts": state["repair_max_attempts"],
                    "required_level": candidate["required_level"],
                    "changed_step_ids": candidate["changed_step_ids"],
                },
            ),
        ]
        if not candidate["requires_approval"]:
            return {
                "repair_candidate": candidate,
                "execution_phase": "repair_apply",
                "public_events": events,
            }
        payload = {
            k: candidate[k]
            for k in (
                "proposal_sha256",
                "attempt",
                "authorized_level",
                "required_level",
                "requires_policy_escalation",
                "summary",
                "failed_step_ids",
                "completed_step_ids",
                "changed_step_ids",
                "steps",
                "source_modified",
                "workflow_eligible",
            )
        }
        payload["max_attempts"] = state["repair_max_attempts"]
        review = {
            "interaction_id": str(uuid4()),
            "revision": 1,
            "kind": "repair_review",
            "status": "open",
            "resume_token": state["agent_run_id"],
            "summary": "실패 원인과 수정할 계획을 확인해 주세요.",
            "payload": payload,
        }
        RepairInteractionData.model_validate(review)
        return {
            "repair_candidate": candidate,
            "repair_review": review,
            "execution_phase": "repair_wait",
            "public_events": [
                *events,
                self.event(state, "interaction.opened", review),
            ],
        }

    @record_user_resume
    def wait(self, state):
        review = state["repair_review"]
        answer = user_interrupt(
            {
                **review,
                "task_id": state["task_id"],
                "execution_id": state["execution_id"],
            }
        )
        if isinstance(answer, dict) and "event" in answer:
            event = ExecutorEvent.model_validate(answer["event"])
            require(
                event.event_type == "execution.completed"
                and str(event.execution_id) == state["execution_id"]
                and answer["task_id"] == state["task_id"],
                "Only a matching terminal event may close a repair wait",
            )
            pending = {**state, "ew_pending": answer}
            return {
                **receipt_update(pending),
                "ew_pending": answer,
                "execution_phase": "event",
                "public_events": [],
            }
        action = validate_repair_action(review, (answer or {}).get("resume"))
        verify_candidate(state["repair_candidate"], state)
        return {
            "repair_review": {**review, "status": "resolved"},
            "repair_action": action.action,
            "execution_phase": "repair_apply"
            if action.action == "approve_repair"
            else "repair_reject",
            "public_events": [],
        }

    def applied(self, state):
        if state["execution_phase"] == "event":
            return {}
        owner = state["user_resume_receipt"]["command_id"]
        current = {**state, "agent_run_id": owner}
        review = state["repair_review"]
        return {
            "agent_run_id": owner,
            "public_events": [
                self.event(
                    current,
                    "interaction.resolved",
                    {
                        "interaction_id": review["interaction_id"],
                        "revision": review["revision"],
                        "kind": "repair_review",
                        "status": "resolved",
                        "resolution": "approved"
                        if state["repair_action"] == "approve_repair"
                        else "rejected",
                        "payload": {
                            "proposal_sha256": review["payload"][
                                "proposal_sha256"
                            ]
                        },
                    },
                )
            ],
        }

    def apply(self, state):
        candidate = state["repair_candidate"]
        verify_candidate(candidate, state)
        history = [
            *state.get("repair_history", []),
            {
                k: deepcopy(candidate[k])
                for k in (
                    "proposal_sha256",
                    "base_snapshot_sha256",
                    "attempt",
                    "authorized_level",
                    "required_level",
                    "summary",
                    "reason",
                    "failed_step_ids",
                    "changed_step_ids",
                    "source_modified",
                    "workflow_eligible",
                )
            },
        ]
        history[-1].update(
            status="accepted",
            execution_snapshot_sha256=candidate["snapshot"]["approval_sha256"],
            after_operation=state["executor_operation_number"],
        )
        events = [
            *state.get("public_events", []),
            self.event(
                state,
                "activity.updated",
                {
                    "kind": "repair",
                    "title": (
                        "확정한 수정 계획으로 후속 작업을 준비합니다. "
                        "성공한 단계는 다시 실행하지 "
                        "않아요."
                    ),
                    "attempt": candidate["attempt"],
                    "required_level": candidate["required_level"],
                },
            ),
        ]
        return {
            "execution_snapshot": candidate["snapshot"],
            "repair_attempts": candidate["attempt"],
            "repair_authorized_level": max(
                candidate["authorized_level"], candidate["required_level"]
            ),
            "repair_history": history,
            "repair_candidate": None,
            "repair_review": None,
            "repair_stop_reason": None,
            "analysis_failure": False,
            "execution_phase": "select",
            "public_events": events,
        }

    def reject(self, state):
        candidate = state["repair_candidate"]
        verify_candidate(candidate, state)
        record = {
            k: candidate[k]
            for k in (
                "proposal_sha256",
                "attempt",
                "required_level",
                "summary",
                "changed_step_ids",
            )
        }
        return {
            **stop(state, "user_rejected"),
            "repair_history": [
                *state.get("repair_history", []),
                {**record, "status": "rejected"},
            ],
        }
