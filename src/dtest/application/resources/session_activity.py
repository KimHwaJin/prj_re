"""One statement snapshot for session metadata, public activity and admission.

No graph/checkpoint/Executor reads and no ORM Run results. Lateral projection
returns at most one row per session; pagination keeps its original tie-breaker.
Only SQL structure is shared, never user/session state or query results.
"""

from sqlalchemy import cast, func, or_, select, true, union
from sqlalchemy.dialects.postgresql import JSONPATH
from sqlalchemy.orm import Bundle, aliased

from dtest.contracts.enums import AgentRunStatus, DeleteYN, TaskStatus
from dtest.infrastructure.database.models.agent_run_model import (
    AgentRunModel as Run,
)
from dtest.infrastructure.database.models.agent_command_model import (
    AgentCommandModel as Command,
)
from dtest.infrastructure.database.models.task_model import TaskModel as Task
from dtest.infrastructure.database.models.session_model import (
    SessionModel as Session,
)
from dtest.infrastructure.database.models.session_execution_model import (
    SessionExecutionModel as Owner,
)
from dtest.application.runs.public_status import public_status, TERMINAL_TASKS
from dtest.application.runs.errors import RunConflict
from dtest.contracts.resources.api_schema import SessionResource
from dtest.contracts.resources.session_activity_schema import (
    SessionActiveRun,
    SessionAvailability,
)
from dtest.application.resources.lifecycle import unfinished_work_conditions


def _projection():
    root, latest = aliased(Run), aliased(Run)
    latest_id = (
        select(Run.run_id)
        .where(Run.public_run_id == root.run_id)
        .order_by(Run.created_at.desc(), Run.run_id.desc())
        .limit(1)
        .correlate(root)
        .scalar_subquery()
    )
    # Start with unfinished Task roots and exceptional active invocation rows.
    # Do not probe latest invocation once for every completed public Run in a
    # session's history. UNION deduplicates roots across Task and Run sources.
    candidates = union(
        select(Task.root_run_id.label("run_id"))
        .where(
            Task.session_id == Session.session_id,
            Task.root_run_id.is_not(None),
            or_(
                Task.status.not_in(TERMINAL_TASKS),
                Task.recovery_required.is_(True),
            ),
        )
        .correlate(Session),
        select(Run.public_run_id.label("run_id"))
        .where(
            Run.session_id == Session.session_id,
            or_(
                Run.status.in_(
                    (AgentRunStatus.PENDING, AgentRunStatus.RUNNING)
                ),
                (Run.status == AgentRunStatus.INTERRUPTED)
                & Run.task_id.is_(None),
            ),
        )
        .correlate(Session),
    ).subquery("active_candidates")
    active = (
        select(
            root.run_id.label("run_id"),
            latest.status.label("invocation_status"),
            Task.task_id,
            Task.status.label("task_status"),
            Task.recovery_required.label("task_recovery"),
            func.jsonb_path_exists(
                latest.interrupt,
                cast('$[*] ? (@.kind == "EXECUTOR_EVENT")', JSONPATH),
            ).label("executor_wait"),
            func.count().over().label("active_count"),
        )
        .select_from(candidates)
        .join(root, root.run_id == candidates.c.run_id)
        .join(latest, latest.run_id == latest_id)
        .outerjoin(Task, Task.task_id == latest.task_id)
        .where(
            root.session_id == Session.session_id,
            root.public_run_id == root.run_id,
            or_(
                Task.status.not_in(TERMINAL_TASKS),
                Task.recovery_required.is_(True),
                latest.status.in_(
                    (AgentRunStatus.PENDING, AgentRunStatus.RUNNING)
                ),
                (latest.status == AgentRunStatus.INTERRUPTED)
                & latest.task_id.is_(None),
            ),
        )
        .order_by(root.created_at.desc(), root.run_id.desc())
        .limit(1)
        .correlate(Session)
        .lateral("session_active_run")
    )

    def exists(model, *conditions):
        return (
            select(1)
            .select_from(model)
            .where(model.session_id == Session.session_id, *conditions)
            .correlate_except(model)
            .exists()
        )

    recovering = or_(
        exists(Task, Task.recovery_required.is_(True)),
        exists(Owner, Owner.recovery_required.is_(True)),
        exists(Command, Command.state == "RECOVERY"),
    )
    canceling = or_(
        exists(
            Task,
            Task.status.not_in(TERMINAL_TASKS),
            Task.cancel_requested_at.is_not(None),
        ),
        exists(
            Run,
            Run.status.in_((AgentRunStatus.PENDING, AgentRunStatus.RUNNING)),
            Run.cancel_requested_at.is_not(None),
        ),
    )
    other_task = exists(
        Task,
        or_(
            Task.status.not_in(TERMINAL_TASKS),
            Task.recovery_required.is_(True),
        ),
        or_(active.c.task_id.is_(None), Task.task_id != active.c.task_id),
    )
    blockers = unfinished_work_conditions(session_id=Session.session_id)
    state = Bundle(
        "activity",
        *active.c,
        recovering.label("recovering"),
        canceling.label("canceling"),
        other_task.label("other_task"),
        *(value.label(key) for key, value in blockers.items()),
    )
    base = select(state).select_from(Session).outerjoin(active, true())
    metadata = Bundle(
        "session",
        Session.session_id,
        Session.project_id,
        Session.session_name,
        Session.current_leaf_message_id,
        Session.settings,
        Session.created_at,
        Session.updated_at,
        Session.delete_yn,
        state,
    )
    return base, base.with_only_columns(metadata)


STATE_QUERY, SESSION_QUERY = _projection()


def describe(state):
    active_run = None
    if state.run_id is not None:
        active_run = SessionActiveRun(
            run_id=state.run_id,
            status=public_status(
                state.invocation_status,
                task_status=state.task_status,
                recovery_required=state.task_recovery,
                executor_wait=bool(state.executor_wait),
            ),
        )
    if state.recovering or (state.active_count or 0) > 1:
        availability = SessionAvailability(
            status="blocked", allowed_actions=[], reason="recovery_required"
        )
    elif state.canceling:
        availability = SessionAvailability(
            status="busy", allowed_actions=[], reason="canceling"
        )
    elif not any(
        (
            state.unfinished_command,
            state.unfinished_task,
            state.unfinished_run,
            state.execution_held_or_uncertain,
        )
    ):
        availability = SessionAvailability(
            status="available", allowed_actions=["send_message"], reason=None
        )
    elif (
        active_run
        and active_run.status == "waiting_input"
        and state.task_status in (None, TaskStatus.WAITING_INPUT)
        and not any(
            (
                state.other_task,
                state.unfinished_command,
                state.execution_held_or_uncertain,
            )
        )
    ):
        availability = SessionAvailability(
            status="available",
            allowed_actions=["respond_to_interaction"],
            reason=None,
        )
    else:
        availability = SessionAvailability(
            status="busy",
            allowed_actions=[],
            reason="waiting_external"
            if active_run and active_run.status == "waiting_executor"
            else "processing",
        )
    return active_run, availability


def resource(row):
    active_run, availability = describe(row.activity)
    if row.delete_yn == DeleteYN.Y:
        availability = SessionAvailability(
            status="blocked", allowed_actions=[], reason="resource_unavailable"
        )
    return SessionResource.model_validate(
        {
            **row._mapping,
            "active_run": active_run,
            "availability": availability,
        }
    )


async def require_input(db, session_id, *, resume_public_id=None):
    """Caller holds admission locks. Idempotent replay must happen before here."""
    state = await db.scalar(
        STATE_QUERY.where(Session.session_id == session_id)
    )
    if state is None:
        raise RunConflict("Session not found.")
    active_run, availability = describe(state)
    action = (
        "send_message"
        if resume_public_id is None
        else "respond_to_interaction"
    )
    if action not in availability.allowed_actions or (
        resume_public_id is not None
        and (active_run is None or active_run.run_id != resume_public_id)
    ):
        raise RunConflict(
            f"Session does not allow {action}; refresh state ({availability.reason or 'waiting_input'})."
        )
