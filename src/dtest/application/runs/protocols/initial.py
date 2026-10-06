"""Guard initial input delivery and recover completed checkpoint projections."""

from sqlalchemy import select

from dtest.application.runs.claim_context import current_execution_claim
from dtest.application.runs.persistence.recovery import (
    GraphProjectionError,
    checkpoint_state,
    snapshot_interrupts,
)
from dtest.contracts.enums import AgentRunStatus
from dtest.contracts.execution import (
    ExecutionNeedsRecovery,
    InvocationNeedsRecovery,
)
from dtest.contracts.initial_request import initial_identity
from dtest.infrastructure.database.models.agent_run_model import AgentRunModel
from dtest.infrastructure.database.runtime import short_session


async def mark_started(*, run_id, session_factory):
    claim = current_execution_claim.get()
    if claim is None or claim.run_id != run_id:
        raise InvocationNeedsRecovery(
            "Initial request requires its Worker claim"
        )
    async with short_session(session_factory) as db:
        run = await db.scalar(
            select(AgentRunModel)
            .where(AgentRunModel.run_id == run_id)
            .with_for_update()
        )
        if (
            run is None
            or run.status != AgentRunStatus.RUNNING
            or run.attempt_count != claim.attempt
            or (run.metadata_json or {}).get("_initial_protocol") != 1
            or (run.metadata_json or {}).get("_initial_started")
        ):
            raise InvocationNeedsRecovery(
                "Initial request admission changed before execution"
            )
        run.metadata_json = {**run.metadata_json, "_initial_started": True}
        await db.commit()


def require_finished(snapshot, identity):
    if snapshot.values.get("initial_request_receipt") != identity:
        raise InvocationNeedsRecovery(
            "Initial request has no matching entry-node receipt"
        )
    if (
        any(task.error for task in snapshot.tasks)
        or (snapshot.next and not snapshot_interrupts(snapshot))
        or any(not task.interrupts for task in snapshot.tasks)
    ):
        raise InvocationNeedsRecovery(
            "Initial request was consumed but graph progress is incomplete"
        )


async def start_and_project(
    graph,
    config,
    graph_input,
    *,
    user_id,
    project_id,
    session_id,
    run_id,
    protocol,
    started,
    session_factory=None,
    dispatcher=None,
    trigger_message_id=None,
    invocation=None,
):
    if protocol != 1:
        raise InvocationNeedsRecovery(
            "Initial request has no delivery protocol; reconcile legacy queue"
        )
    identity = initial_identity(graph_input)
    snapshot = await graph.aget_state(config)
    values = snapshot.values or {}
    receipt = values.get("initial_request_receipt")
    if isinstance(receipt, dict) and receipt.get("command_id") == str(run_id):
        require_finished(snapshot, identity)
        invocation.validate_model(values, graph_input["model_selection"])
    else:
        if started or values.get("run_id") == str(run_id):
            raise InvocationNeedsRecovery(
                "Initial input may have been delivered without a "
                "receipt; do not "
                "resend"
            )
        # An admitted new Run may replace an old terminal/cancelled task's
        # checkpoint. Existing session admission/ownership prevents a live writer.
        prepared = {
            **graph_input,
            **await invocation.project_snapshot(
                user_id=user_id,
                session_id=session_id,
                project_id=project_id,
            ),
            "initial_request_identity": identity,
            "initial_request_receipt": None,
        }
        await mark_started(run_id=run_id, session_factory=session_factory)
        await invocation.invoke(
            prepared,
            config,
            user_id=user_id,
            agent_run_id=run_id,
            trigger_message_id=trigger_message_id,
            accept_state=lambda state: (
                state.get("initial_request_receipt") == identity
            ),
        )
        snapshot = await graph.aget_state(config)
        require_finished(snapshot, identity)
    try:
        return await invocation.project(
            checkpoint_state(snapshot),
            user_id=user_id,
            agent_run_id=run_id,
            trigger_message_id=trigger_message_id,
        )
    except (ExecutionNeedsRecovery, InvocationNeedsRecovery):
        raise
    except Exception as exc:
        raise GraphProjectionError(
            "Durable initial request needs service projection"
        ) from exc
