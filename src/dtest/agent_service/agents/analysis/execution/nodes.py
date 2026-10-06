"""Durable Operation boundaries; network calls never wait for code execution."""

from uuid import uuid4

from langgraph.graph import END

from dtest.agent_service.agents.analysis.state import NODE_INPUTS
from dtest.agent_service.middleware.prompt_json import StructuredResponseError
from dtest.agent_service.runtime.analysis_scope import execution_scope
from dtest.agent_service.runtime.blocking import run_sync
from dtest.agent_service.runtime.executor_boundary import (
    ExecutorBoundaryNodes,
    receipt_update,
)
from dtest.agent_service.runtime.session_analysis import capture_analysis
from dtest.agent_service.runtime.user_resume import (
    record_user_resume,
    user_interrupt,
)
from dtest.contracts.events import ExecutorEvent
from dtest.contracts.execution_review import validate_decision_action
from dtest.contracts.executor import (
    ExecutorCancelRequestBody,
    ExecutorContinueRequestBody,
    ExecutorFinalizeRequestBody,
    ExecutorRequestBody,
)
from dtest.contracts.plan_interaction import DecisionInteractionData
from dtest.contracts.plan_review import require
from dtest.infrastructure.executor.client import (
    submit_execution_cancel,
    submit_execution_continue,
    submit_execution_finish,
    submit_execution_start,
)
from dtest.infrastructure.executor.observations import (
    public_observations,
    read_operation_observations,
)

from .compiler import (
    compile_steps,
    materialize_steps,
    ready_batch,
    validate_decisions,
)
from .repair_nodes import RepairNodes
from .repair_policy import effective_snapshot
from .report import render_evidence_markdown


class ExecutionNodes:
    def __init__(self, runtime, event_factory):
        self.runtime, self.event = runtime, event_factory
        self.boundary = ExecutorBoundaryNodes(runtime.bindings)

    async def select(self, state):
        snapshot = effective_snapshot(state)
        complete, skipped, decisions = (
            state.get("completed_steps", []),
            state.get("skipped_steps", []),
            state.get("execution_decisions", {}),
        )
        evidence = {
            o["step_id"]: o["summary"]
            for o in state.get("observations", [])
            if o["status"] == "SUCCEEDED"
        }
        batch, skipped = ready_batch(
            snapshot, complete, skipped, decisions, evidence
        )
        result = {"skipped_steps": skipped, "final_response": None}
        if not state.get("execution_id"):
            result.update(
                repair_authorized_level=snapshot["execution"]["repair_level"],
                repair_max_attempts=snapshot["execution"][
                    "max_repair_attempts"
                ],
            )
        if batch:
            number = state.get("executor_operation_number", 0) + 1
            if number > self.runtime.settings.agent_max_operations:
                require(
                    state.get("execution_id"),
                    "Operation budget does not allow initial submission",
                )
                return {
                    **result,
                    "execution_phase": "cancel",
                    "analysis_failure": True,
                    "execution_error": {
                        "message": "Configured Operation budget exceeded"
                    },
                }
            start = state.get("next_step_sequence", 0)
            steps = compile_steps(snapshot, batch, decisions, start)
            if self.runtime.settings.executor_source_type == "PATH":
                steps = await run_sync(
                    materialize_steps,
                    self.runtime.settings.executor_shared_input_root,
                    steps,
                )
            actor = {"type": "AGENT", "id": "analysis"}
            key = f"{snapshot['run_id']}:approval:{state['approved_snapshot']['approval_sha256']}:operation:{number}"
            metadata = {
                "plan_id": snapshot["plan_id"],
                "plan_revision": snapshot["plan_revision"],
                "public_run_id": snapshot["run_id"],
                "approval_sha256": state["approved_snapshot"][
                    "approval_sha256"
                ],
                "execution_snapshot_sha256": snapshot["approval_sha256"],
                "repair_attempt": state.get("repair_attempts", 0),
                "logical_steps": [
                    {"sequence": s["sequence"], "plan_step_id": p["id"]}
                    for s, p in zip(steps, batch)
                ],
            }
            if not state.get("execution_id"):
                lifecycle = {"operation_mode": snapshot["execution"]["mode"]}
                if lifecycle["operation_mode"] == "MULTI":
                    lifecycle["operation_wait_timeout_seconds"] = (
                        self.runtime.settings.executor_operation_wait_timeout_seconds
                    )
                ctx = {
                    k: snapshot["context"][k]
                    for k in ("user_id", "project_id", "session_id")
                }
                ctx.update(
                    task_id=state["task_id"],
                    workflow_id=snapshot["document"]["workflow_id"],
                )
                payload = ExecutorRequestBody.model_validate(
                    {
                        "idempotency_key": key,
                        "lifecycle": lifecycle,
                        "trigger": {"type": "INTERACTIVE", "actor": actor},
                        "runtime": {
                            "type": "JUPYTER",
                            "profile": snapshot["context"]["kernel_profile"],
                        },
                        "context": ctx,
                        "operation": {
                            "operation_timeout_seconds": self.runtime.settings.executor_operation_timeout_seconds,
                            "spec": {"steps": steps},
                            "metadata": metadata,
                        },
                    }
                ).model_dump(mode="json", exclude_none=True)
            else:
                payload = ExecutorContinueRequestBody.model_validate(
                    {
                        "idempotency_key": key,
                        "expected_version": state["executor_version"],
                        "operation_timeout_seconds": self.runtime.settings.executor_operation_timeout_seconds,
                        "spec": {"steps": steps},
                        "metadata": metadata,
                        "actor": actor,
                    }
                ).model_dump(mode="json", exclude_none=True)
            # Checkpoint the exact payload BEFORE delivery. Retries reuse this identity and body.
            result.update(
                execution_command=payload,
                executor_operation_number=number,
                submitted_steps=[
                    {
                        "sequence": s["sequence"],
                        "plan_step_id": p["id"],
                        "tool_id": p["tool_id"],
                        "repair_attempt": state.get("repair_attempts", 0),
                    }
                    for s, p in zip(steps, batch)
                ],
                next_step_sequence=start + len(steps),
                execution_phase="submit",
            )
            return result
        remaining = [
            s
            for s in snapshot["steps"]
            if s["id"] not in set(complete) | set(skipped)
        ]
        if not remaining:
            result["execution_phase"] = (
                "finalize"
                if snapshot["execution"]["mode"] == "MULTI"
                else "wait"
            )
            return result
        from dtest.contracts.workflow_validation import bindings

        needed = {
            b["decision_id"]
            for s in remaining
            for b in bindings([s["arguments"], s.get("when")])
            if b["source"] == "agent_decision"
            and b["decision_id"] not in decisions
        }
        pending = [
            d
            for d in snapshot["document"]["decisions"]
            if d["id"] in needed and set(d["after_steps"]) <= set(complete)
        ]
        require(
            pending, "Approved plan cannot progress with available evidence"
        )
        result.update(pending_decisions=pending, execution_phase="review")
        return result

    async def submit(self, state):
        settings = self.runtime.settings
        if state.get("execution_id"):
            response = await submit_execution_continue(
                settings,
                state["execution_id"],
                state["execution_command"],
                client=self.runtime.executor,
            )
        else:
            response = await submit_execution_start(
                settings,
                state["execution_command"],
                client=self.runtime.executor,
            )
        body = response["body"]
        require(
            not state.get("execution_id")
            or body["execution_id"] == state["execution_id"],
            "Executor receipt identity mismatch",
        )
        receipt = {
            s["sequence"]: s["step_id"] for s in body["operation"]["steps"]
        }
        require(
            receipt.keys()
            == {s["sequence"] for s in state["submitted_steps"]},
            "Executor receipt Step mapping differs",
        )
        steps = [
            {**s, "executor_step_id": receipt[s["sequence"]]}
            for s in state["submitted_steps"]
        ]
        events = [
            *state.get("public_events", []),
            self.event(
                state,
                "activity.updated",
                {
                    "kind": "executor",
                    "title": f"분석 작업 {state['executor_operation_number']}을 제출했습니다. 실행 결과를 기다리고 있어요.",
                    "execution_id": body["execution_id"],
                    "operation_number": state["executor_operation_number"],
                },
            ),
        ]
        history = [dict(row) for row in state.get("repair_history", [])]
        if (
            history
            and history[-1].get("status") == "accepted"
            and history[-1]["after_operation"] + 1
            == state["executor_operation_number"]
        ):
            history[-1].update(
                operation_id=body["operation"]["operation_id"],
                operation_number=state["executor_operation_number"],
                outcome="pending",
            )
        return {
            "repair_history": history,
            "execution_id": body["execution_id"],
            "executor_version": body["state"]["version"],
            "executor_operation_id": body["operation"]["operation_id"],
            "submitted_steps": steps,
            "execution_phase": "wait",
            "executor_wait_phase": "operation_completed",
            "public_events": events,
        }

    async def register(self, state, config):
        return await self.boundary.register_execution(state, config)

    def wait(self, state):
        return {
            **self.boundary.wait_executor_event(state),
            "public_events": [],
        }

    def receipt(self, state):
        return receipt_update(state)

    async def process_event(self, state):
        event = ExecutorEvent.model_validate(
            state["ew_pending"]["event"]
        ).model_dump(mode="json")
        payload = event["payload"]
        if event["event_type"] == "execution.completed":
            return {
                "execution_status": payload["status"],
                "execution_phase": "report",
                "terminal_event_seen": True,
                "execution_error": payload.get("error"),
                "decision_review": None,
                "repair_review": None,
                "repair_candidate": None,
                "repair_stop_reason": state.get("repair_stop_reason")
                or (
                    "executor_terminal_during_repair"
                    if state.get("repair_review")
                    else None
                ),
            }
        require(
            event["event_type"] == "execution.operation_completed",
            "Unsupported Executor boundary",
        )
        require(
            payload["operation"]["id"] == state["executor_operation_id"],
            "Completed Operation does not match receipt",
        )
        facts = await run_sync(
            read_operation_observations,
            self.runtime.settings,
            event,
            state["submitted_steps"],
        )
        completed = list(
            dict.fromkeys(
                [
                    *state.get("completed_steps", []),
                    *[
                        o["step_id"]
                        for o in facts
                        if o["status"] == "SUCCEEDED"
                    ],
                ]
            )
        )
        observations = [*state.get("observations", []), *facts]
        history = [dict(row) for row in state.get("repair_history", [])]
        for row in history:
            if row.get("operation_id") == payload["operation"]["id"]:
                row["outcome"] = payload["status"].lower()
        result = {
            "completed_steps": completed,
            "observations": observations,
            "repair_history": history,
            "public_events": [
                self.event(
                    state,
                    "activity.updated",
                    {
                        "kind": "executor",
                        "title": f"분석 작업 {state['executor_operation_number']}의 결과를 받았습니다.",
                        "operation_number": state["executor_operation_number"],
                        "steps": public_observations(facts),
                    },
                )
            ],
        }
        continuation = payload.get("continuation")
        multi = state["approved_snapshot"]["execution"]["mode"] == "MULTI"
        if not multi or not continuation or not continuation.get("allowed"):
            result.update(
                execution_phase="wait",
                executor_wait_phase="execution_completed",
            )
        elif payload["status"] != "SUCCEEDED":
            failed = [
                o["step_id"]
                for o in facts
                if o["status"] == "FAILED" and not o["incomplete"]
            ]
            code = (payload.get("error") or {}).get("code")
            eligible = (
                payload["status"] == "FAILED"
                and bool(failed)
                and code in {None, "OPERATION_STEP_FAILED"}
                and state.get("repair_authorized_level", 0) > 0
                and state.get("repair_max_attempts", 0) > 0
                and state.get("repair_attempts", 0)
                < state["repair_max_attempts"]
            )
            result.update(
                execution_phase="repair_propose" if eligible else "cancel",
                analysis_failure=True,
                execution_error=payload.get("error"),
                failed_step_ids=failed,
                executor_version=continuation["expected_version"],
                repair_stop_reason=None
                if eligible
                else "attempts_exhausted"
                if state.get("repair_authorized_level", 0) > 0
                and state.get("repair_attempts", 0)
                >= state.get("repair_max_attempts", 0)
                else "repair_disabled_or_unsupported_failure",
            )
        else:
            result.update(
                execution_phase="select",
                executor_version=continuation["expected_version"],
            )
            if (
                state["approved_snapshot"]["execution"]["review_mode"]
                != "decision_boundary"
            ):
                snapshot = effective_snapshot(state)
                from dtest.contracts.workflow_validation import bindings

                remaining = [
                    s
                    for s in snapshot["steps"]
                    if s["id"]
                    not in set(completed) | set(state.get("skipped_steps", []))
                ]
                needed = {
                    b["decision_id"]
                    for s in remaining
                    for b in bindings([s["arguments"], s.get("when")])
                    if b["source"] == "agent_decision"
                    and b["decision_id"]
                    not in state.get("execution_decisions", {})
                }
                pending = [
                    d
                    for d in snapshot["document"]["decisions"]
                    if d["id"] in needed
                    and set(d["after_steps"]) <= set(completed)
                ]
                result.update(
                    execution_phase="review", pending_decisions=pending
                )
        return result

    async def review(self, state):
        snapshot = effective_snapshot(state)
        error = None
        try:
            response = await self.runtime.execution_role(
                "review",
                state,
                {
                    "goal": snapshot["document"]["goal"],
                    "pending_decisions": state.get("pending_decisions", []),
                    "observations": self.role_facts(state),
                    "skills": snapshot["skill_sources"],
                    "approved_steps": [
                        {k: s[k] for k in ("id", "tool_id", "description")}
                        for s in snapshot["steps"]
                    ],
                },
            )
        except StructuredResponseError as exc:
            from dtest.agent_service.agents.analysis.agent_builders.execution_review.agent import (
                ReviewResponse,
            )

            error = str(exc)[:2000]
            response = ReviewResponse(
                choices=[],
                needs_user_input=True,
                message=(
                    "실행 결과에 근거한 판단값을 검증하지 못했습니다. "
                    "다음 단계의 입력값을 확인해 "
                    "주세요."
                ),
            )
        proposed = {
            choice.decision_id: choice.value for choice in response.choices
        }
        pending = {d["id"]: d for d in state.get("pending_decisions", [])}
        safe = (
            len(proposed) == len(response.choices)
            and proposed.keys() <= pending.keys()
            and all(
                set(c.evidence_steps) <= set(state["completed_steps"])
                and set(pending[c.decision_id]["after_steps"])
                <= set(c.evidence_steps)
                for c in response.choices
            )
        )
        try:
            require(safe, "Decision has unapproved or incomplete evidence")
            validate_decisions(snapshot, proposed, state["completed_steps"])
        except (ValueError, TypeError):
            proposed = {}
            needs_input = True
        else:
            needs_input = (
                response.needs_user_input or proposed.keys() != pending.keys()
            )
        if needs_input:
            interaction_id = str(uuid4())
            review = {
                "interaction_id": interaction_id,
                "revision": 1,
                "kind": "decision_review",
                "status": "open",
                "resume_token": state["agent_run_id"],
                "summary": response.message,
                "payload": {
                    "decisions": [
                        {
                            "decision_id": d["id"],
                            "guidance": d["instruction"],
                            "evidence_steps": d["after_steps"],
                            "value_schema": d["output_schema"],
                            "has_value": d["id"] in proposed,
                            **(
                                {"value": proposed[d["id"]]}
                                if d["id"] in proposed
                                else {}
                            ),
                        }
                        for d in pending.values()
                    ]
                },
            }
            DecisionInteractionData.model_validate(review)
            return {
                "decision_review": review,
                "execution_phase": "decision_wait",
                "execution_review_validation_error": error,
                "public_events": [
                    *state.get("public_events", []),
                    self.event(state, "interaction.opened", review),
                ],
            }
        decisions = {**state.get("execution_decisions", {}), **proposed}
        return {
            "execution_decisions": decisions,
            "pending_decisions": [],
            "execution_phase": "select",
            "execution_review_validation_error": None,
            "public_events": [
                *state.get("public_events", []),
                self.event(
                    state,
                    "message.completed",
                    {
                        "role": "assistant",
                        "channel": "commentary",
                        "content": [
                            {"type": "text", "text": response.message}
                        ],
                    },
                ),
            ],
        }

    @record_user_resume
    def decision_wait(self, state):
        review = state["decision_review"]
        answer = user_interrupt(
            {
                **review,
                "task_id": state["task_id"],
                "execution_id": state.get("execution_id"),
            }
        )
        if isinstance(answer, dict) and "event" in answer:
            event = ExecutorEvent.model_validate(answer["event"])
            require(
                event.event_type == "execution.completed"
                and str(event.execution_id) == state["execution_id"]
                and answer["task_id"] == state["task_id"],
                "Only a matching terminal event may close a decision wait",
            )
            pending = {**state, "ew_pending": answer}
            return {
                **receipt_update(pending),
                "ew_pending": answer,
                "execution_phase": "event",
                "public_events": [],
            }
        values = validate_decision_action(review, (answer or {}).get("resume"))
        return {
            "execution_decisions": {
                **state.get("execution_decisions", {}),
                **values,
            },
            "decision_review": {**review, "status": "resolved"},
            "execution_phase": "select",
            "public_events": [],
        }

    def decision_applied(self, state):
        if state["execution_phase"] == "event":
            return {}
        owner = state["user_resume_receipt"]["command_id"]
        current = {**state, "agent_run_id": owner}
        review = state["decision_review"]
        return {
            "agent_run_id": owner,
            "public_events": [
                self.event(
                    current,
                    "interaction.resolved",
                    {
                        "interaction_id": review["interaction_id"],
                        "revision": review["revision"],
                        "kind": "decision_review",
                        "status": "resolved",
                        "resolution": "approved",
                        "payload": {"values": state["execution_decisions"]},
                    },
                )
            ],
        }

    async def finalize(self, state):
        snapshot = effective_snapshot(state)
        payload = ExecutorFinalizeRequestBody(
            idempotency_key=f"{snapshot['run_id']}:approval:{state['approved_snapshot']['approval_sha256']}:finalize",
            expected_version=state["executor_version"],
            actor={"type": "AGENT", "id": "analysis"},
        ).model_dump(mode="json")
        await submit_execution_finish(
            self.runtime.settings,
            state["execution_id"],
            payload,
            client=self.runtime.executor,
        )
        return {
            "executor_wait_phase": "execution_completed",
            "execution_phase": "wait",
            "public_events": [
                *state.get("public_events", []),
                self.event(
                    state,
                    "activity.updated",
                    {
                        "kind": "executor",
                        "title": (
                            "분석을 마쳤습니다. 실행 환경의 최종 정리를 "
                            "기다리고 있어요."
                        ),
                    },
                ),
            ],
        }

    async def cancel(self, state):
        snapshot = effective_snapshot(state)
        payload = ExecutorCancelRequestBody(
            idempotency_key=f"{snapshot['run_id']}:approval:{state['approved_snapshot']['approval_sha256']}:stop-on-failure",
            reason="Approved execution cannot continue within its policy",
            actor={"type": "AGENT", "id": "analysis"},
        ).model_dump(mode="json")
        await submit_execution_cancel(
            self.runtime.settings,
            state["execution_id"],
            payload,
            client=self.runtime.executor,
        )
        return {
            "executor_wait_phase": "execution_completed",
            "execution_phase": "wait",
        }

    def role_facts(self, state):
        # Preserve quantitative summaries; printed logs are explicitly bounded untrusted evidence.
        return [
            {**o, "text": raw.get("text", [])[:2]}
            for o, raw in zip(
                public_observations(state.get("observations", [])),
                state.get("observations", []),
            )
        ]

    async def report(self, state):
        require(
            state.get("terminal_event_seen"),
            "No report before the terminal Executor event",
        )
        snapshot = effective_snapshot(state)
        facts = public_observations(state.get("observations", []))
        success = state["execution_status"] == "SUCCEEDED" and not state.get(
            "analysis_failure"
        )
        wants_report = any(
            o["kind"] == "report"
            for o in snapshot["document"]["expected_outputs"]
        )
        report_evidence = []
        report_status = "ready" if wants_report else "not_requested"
        if wants_report and facts:
            try:
                response = await self.runtime.execution_role(
                    "report",
                    state,
                    {
                        "requested_goal": snapshot["document"]["goal"],
                        "execution_scope": execution_scope(
                            state,
                            snapshot,
                            facts,
                            state.get("skipped_steps", []),
                        ),
                        "observations": self.role_facts(state),
                        "decisions": state.get("execution_decisions", {}),
                        "skipped_steps": state.get("skipped_steps", []),
                        "execution_status": state["execution_status"],
                    },
                )
            except StructuredResponseError:
                # Execution already ended. Invalid interpretation must not strand its session.
                report_status = "evidence_only"
                narrative = (
                    "# 분석 실행 결과\n\n모델 해석문을 검증하지 못해 실행 "
                    "근거만 제공합니다."
                )
            else:
                require(
                    set(response.evidence_steps)
                    <= {
                        o["step_id"]
                        for o in facts
                        if o["status"] == "SUCCEEDED"
                    },
                    "Report cites unexecuted evidence",
                )
                narrative = response.markdown
                report_evidence = response.evidence_steps
            text = narrative + "\n\n" + render_evidence_markdown(facts)
            report_evidence = sorted(
                set(report_evidence)
                | {o["step_id"] for o in facts if o["status"] == "SUCCEEDED"}
            )
        else:
            text = (
                "승인된 분석을 완료했습니다."
                if success
                else (
                    "분석 실행이 완료되지 않았습니다. 확인된 단계 결과를 "
                    "보존했습니다."
                )
            )
        final = {
            "status": "analysis_completed" if success else "analysis_failed",
            "execution_id": state["execution_id"],
            "executor_status": state["execution_status"],
            "planning": {
                "revision_count": state.get("planning_revision_count", 0),
                "execution_kind": snapshot.get("execution_kind", "registered"),
                "approval_mode": snapshot.get("approval_mode", "user"),
                "workflow_eligible": not any(
                    s["tool_id"].startswith("custom.")
                    for s in snapshot["steps"]
                ),
            },
            "observations": facts,
            "skipped_steps": state.get("skipped_steps", []),
            "repair": {
                "attempts": state.get("repair_attempts", 0),
                "max_attempts": state.get("repair_max_attempts", 0),
                "authorized_level": state.get("repair_authorized_level", 0),
                "stop_reason": state.get("repair_stop_reason"),
                "history": [
                    {
                        **{
                            k: r[k]
                            for k in (
                                "attempt",
                                "required_level",
                                "summary",
                                "changed_step_ids",
                                "status",
                            )
                        },
                        "outcome": r.get("outcome", "not_submitted"),
                    }
                    for r in state.get("repair_history", [])
                ],
                "workflow_eligible": not any(
                    s["tool_id"].startswith("custom.")
                    for s in snapshot["steps"]
                ),
            },
            "report": {
                "format": "markdown",
                "content": text,
                "evidence_steps": report_evidence,
                "status": report_status,
                "validation_scope": "step_ids_and_rendered_facts",
                "artifact_registration": "deferred",
            }
            if wants_report
            else None,
        }
        return {
            "final_response": final,
            "report_status": report_status,
            "last_analysis_context": capture_analysis(
                state,
                snapshot,
                final,
                self.runtime.settings.agent_session_analysis_max_chars,
            ),
            "public_events": [
                *state.get("public_events", []),
                self.event(
                    state,
                    "message.completed",
                    {
                        "role": "assistant",
                        "channel": "answer",
                        "content": [{"type": "text", "text": text}],
                    },
                ),
            ],
        }


def wire_execution(builder, runtime, event_factory):
    nodes = ExecutionNodes(runtime, event_factory)
    for name in (
        "select",
        "submit",
        "register",
        "wait",
        "receipt",
        "process_event",
        "review",
        "decision_wait",
        "decision_applied",
        "finalize",
        "cancel",
        "report",
    ):
        builder.add_node(
            "execution_" + name,
            getattr(nodes, name),
            input_schema=NODE_INPUTS["execution_" + name],
        )
    repair = RepairNodes(runtime, event_factory)
    for name in ("propose", "wait", "applied", "apply", "reject"):
        builder.add_node(
            "execution_repair_" + name,
            getattr(repair, name),
            input_schema=NODE_INPUTS["execution_repair_" + name],
        )
    routes = {
        k: "execution_" + v
        for k, v in {
            "submit": "submit",
            "review": "review",
            "finalize": "finalize",
            "wait": "wait",
            "select": "select",
            "cancel": "cancel",
            "report": "report",
            "decision_wait": "decision_wait",
            "event": "process_event",
            "repair_propose": "repair_propose",
            "repair_wait": "repair_wait",
            "repair_apply": "repair_apply",
            "repair_reject": "repair_reject",
        }.items()
    }
    # Declaring destinations adds graph inspection metadata without changing the
    # existing execution_phase → node routing or persisted node names.
    destinations = {
        "select": ("submit", "review", "finalize", "wait", "cancel"),
        "process_event": (
            "report",
            "wait",
            "repair_propose",
            "cancel",
            "select",
            "review",
        ),
        "review": ("decision_wait", "select"),
        "decision_applied": ("event", "select"),
        "repair_propose": ("cancel", "repair_apply", "repair_wait"),
        "repair_applied": ("event", "repair_apply", "repair_reject"),
        "repair_apply": ("select",),
        "repair_reject": ("cancel",),
    }
    for name, phases in destinations.items():
        builder.add_conditional_edges(
            "execution_" + name,
            lambda s: routes[s["execution_phase"]],
            [routes[phase] for phase in phases],
        )
    builder.add_edge("execution_repair_wait", "execution_repair_applied")
    builder.add_edge("execution_submit", "execution_register")
    builder.add_edge("execution_register", "execution_wait")
    builder.add_edge("execution_wait", "execution_receipt")
    builder.add_edge("execution_receipt", "execution_process_event")
    builder.add_edge("execution_decision_wait", "execution_decision_applied")
    builder.add_edge("execution_finalize", "execution_wait")
    builder.add_edge("execution_cancel", "execution_wait")
    builder.add_edge("execution_report", END)
