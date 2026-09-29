"""Execute an addressed user command once, then repair only its projections.

The immutable target is copied from the preceding Run at admission. A durable
started marker makes a failed invocation without a receipt ambiguous: never
blindly resend it, even if the old interrupt is still visible.
"""
from __future__ import annotations

from sqlalchemy import select
from langgraph.types import Command

from api_service.core.database import short_session
from api_service.core.execution_claim import current_execution_claim
from api_service.core.enums import AgentRunStatus
from api_service.models.common.agent_run_model import AgentRunModel
from api_service.services import graph_crud_persistence as projection
from service_contracts.execution import ExecutionNeedsRecovery
from service_contracts.user_resume import UserResumeNeedsRecovery, resume_envelope, resume_identity
from service_runtime.model_selection import validate_checkpoint_selection


class ResumeProjectionError(RuntimeError):
    """Graph progress is durable; only service projection may be retried."""


def snapshot_interrupts(snapshot):
    return [item for task in snapshot.tasks for item in task.interrupts]


def checkpoint_state(snapshot):
    """Never retain a stale __interrupt__ value from a root dict channel."""
    state = dict(snapshot.values)
    state.pop("__interrupt__", None)
    interrupts = snapshot_interrupts(snapshot)
    if interrupts:
        state["__interrupt__"] = tuple(interrupts)
    return state


async def mark_started(*, run_id, identity, session_factory):
    claim = current_execution_claim.get()
    if claim is None or claim.run_id != run_id:
        raise UserResumeNeedsRecovery("User resume requires its Worker claim")
    async with short_session(session_factory) as db:
        run = await db.scalar(select(AgentRunModel).where(AgentRunModel.run_id == run_id).with_for_update())
        if (run is None or run.status != AgentRunStatus.RUNNING
                or run.attempt_count != claim.attempt
                or (run.metadata_json or {}).get("_resume_target") != identity["interrupt_id"]
                or (run.metadata_json or {}).get("_resume_started")):
            raise UserResumeNeedsRecovery("User resume admission changed before execution")
        run.metadata_json = {**run.metadata_json, "_resume_started": True}
        await db.commit()


def require_finished(snapshot, identity):
    if snapshot.values.get("user_resume_receipt") != identity:
        raise UserResumeNeedsRecovery("User resume has no matching durable consumption receipt")
    interrupts = snapshot_interrupts(snapshot)
    if (any(task.error for task in snapshot.tasks)
            or (snapshot.next and not interrupts)
            or any(not task.interrupts for task in snapshot.tasks)
            or any(item.id == identity["interrupt_id"] for item in interrupts)):
        raise UserResumeNeedsRecovery("User answer was consumed but graph progress is incomplete")


async def resume_and_project(graph, config, *, user_id, run_id, command,
                             target, started, model_selection, session_factory=None,
                             dispatcher=None):
    if not target or not run_id:
        raise UserResumeNeedsRecovery("User resume target is missing; reconcile the legacy waiting Run")
    identity = resume_identity(str(run_id), target, command)
    snapshot = await graph.aget_state(config)
    validate_checkpoint_selection(snapshot.values, model_selection)
    receipt = snapshot.values.get("user_resume_receipt")
    if isinstance(receipt, dict) and receipt.get("command_id") == str(run_id):
        require_finished(snapshot, identity)
    else:
        if started:
            raise UserResumeNeedsRecovery("User resume was dispatched without a durable receipt; do not resend")
        interrupts = snapshot_interrupts(snapshot)
        if len(interrupts) != 1 or interrupts[0].id != target:
            raise UserResumeNeedsRecovery("User resume target no longer matches the checkpoint")
        if isinstance(interrupts[0].value, dict) and interrupts[0].value.get("kind") == "EXECUTOR_EVENT":
            raise UserResumeNeedsRecovery("User resume cannot answer an Executor interrupt")
        await mark_started(run_id=run_id, identity=identity, session_factory=session_factory)
        # If this raises, a queue retry may only inspect the receipt. It may not
        # dispatch this Command again. External submission uncertainty propagates
        # through the existing submission_scope/ExecutionNeedsRecovery guard.
        await graph.ainvoke(
            Command(resume={target: resume_envelope(identity, command)}),
            config=config, durability="sync",
        )
        snapshot = await graph.aget_state(config)
        require_finished(snapshot, identity)
    try:
        return await projection.persist_graph_state(
            checkpoint_state(snapshot), user_id=user_id, session_factory=session_factory,
            dispatcher=dispatcher, agent_run_id=run_id,
        )
    except (ExecutionNeedsRecovery, UserResumeNeedsRecovery):
        raise
    except Exception as exc:
        raise ResumeProjectionError("Durable user resume needs service projection") from exc
