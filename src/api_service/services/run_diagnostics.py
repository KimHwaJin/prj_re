"""Read-only Run projections. Never claim, renew, expire or recover work here.

Only immutable SQL structure is reused. Every call reads current ownership and
visibility. Admin scope is used only by the separately authorized admin routes.
"""
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import bindparam, func, or_, select
from sqlalchemy.orm import Bundle, aliased

from api_service.core.enums import DeleteYN
from api_service.core.pagination import fetch_page
from api_service.models.common.agent_run_model import AgentRunModel as Run
from api_service.models.common.project_model import ProjectModel as Project
from api_service.models.common.session_execution_model import SessionExecutionModel as Owner
from api_service.models.common.session_model import SessionModel as Session
from api_service.models.common.task_model import TaskModel as Task
from api_service.models.common.user_model import UserModel as User
from api_service.schemas.common.run_diagnostics_schema import (
    RunDiagnosticsResource, RunInvocationResource, TaskDiagnostics,
    SessionExecutionDiagnostics, SessionWorkDiagnostics,
)
from api_service.services.resource_lifecycle import TERMINAL_TASKS, unfinished_work_conditions

TASK_FIELDS = (
    "task_id", "graph_task_id", "root_run_id", "checkpoint_run_id",
    "trigger_message_id", "trigger_type", "status", "lock_owner", "heartbeat_at",
    "lease_expires_at", "cancel_requested_at", "failure_reason", "recovery_required",
    "created_at", "updated_at", "completed_at",
)
INVOCATION_FIELDS = (
    "task_id", "session_id", "status", "attempt_count", "next_attempt_at",
    "cancel_requested_at", "cancel_reason", "failure", "created_at", "updated_at",
    "started_at", "completed_at",
)


def _resources_active():
    return (Session.delete_yn == DeleteYN.N) & (Project.delete_yn == DeleteYN.N) & (User.delete_yn == DeleteYN.N)


def _build_queries(*, owner_scoped):
    root, requested, latest = aliased(Run), aliased(Run), aliased(Run)
    identity = select(root.run_id, root.session_id).select_from(root).join(
        requested, requested.public_run_id == root.run_id,
    ).join(Session, Session.session_id == root.session_id).join(
        Project, Project.project_id == Session.project_id,
    ).join(User, User.user_id == Session.user_id).where(
        requested.run_id == bindparam("requested_run_id"),
        requested.session_id == bindparam("requested_session_id"),
        root.session_id == bindparam("requested_session_id"), root.public_run_id == root.run_id,
    )
    if owner_scoped:
        identity = identity.where(Session.user_id == bindparam("user_id"), _resources_active())
    latest_id = select(Run.run_id).where(
        Run.public_run_id == root.run_id, Run.session_id == root.session_id,
    ).order_by(Run.created_at.desc(), Run.run_id.desc()).limit(1).correlate(root).scalar_subquery()
    blockers = unfinished_work_conditions(session_id=Session.session_id)
    diagnostic = identity.join(latest, latest.run_id == latest_id).outerjoin(
        Task, (Task.task_id == latest.task_id) & (Task.session_id == root.session_id) &
        or_(Task.root_run_id.is_(None), Task.root_run_id == root.run_id),
    ).outerjoin(Owner, Owner.session_id == Session.session_id).with_only_columns(
        Bundle("diagnostic", root.run_id, root.session_id,
            Bundle("task", *(getattr(Task, field) for field in TASK_FIELDS)),
            _resources_active().label("resources_active"), func.statement_timestamp().label("observed_at"),
            Bundle("blockers", *(condition.label(name) for name, condition in blockers.items())),
            Bundle("execution", Owner.token.is_not(None).label("ownership_held"),
                Owner.owner_kind, Owner.owner_id, Owner.owner_process,
                Owner.acquired_at, Owner.heartbeat_at, Owner.recovery_required, Owner.recovery_reason),
        ), maintain_column_froms=True,
    )
    # Scope each page again: a visibility change between identity and page
    # queries cannot disclose another owner's or a hidden resource's records.
    invocations = identity.join(Run, (Run.public_run_id == root.run_id) &
        (Run.session_id == root.session_id)).with_only_columns(
        Bundle("invocation", Run.run_id, Run.run_id.label("invocation_id"),
            Run.public_run_id, *(getattr(Run, field) for field in INVOCATION_FIELDS)),
        maintain_column_froms=True,
    )
    return identity, diagnostic, invocations


OWNER_QUERIES = _build_queries(owner_scoped=True)
ADMIN_QUERIES = _build_queries(owner_scoped=False)


def _params(session_id, run_id, user_id):
    return {"requested_session_id": session_id, "requested_run_id": run_id, "user_id": user_id}


def _resource(row):
    owned, recovery = row.execution.ownership_held, bool(row.execution.recovery_required)
    details = dict(row.execution._mapping) if owned or recovery else {}
    details.update(ownership_held=owned, recovery_required=recovery)
    reasons = [name for name, blocked in row.blockers._mapping.items() if blocked]
    unfinished = bool(reasons)
    if not row.resources_active:
        reasons.insert(0, "resources_inactive")
    task = row.task
    return RunDiagnosticsResource(
        run_id=row.run_id, session_id=row.session_id, observed_at=row.observed_at,
        task=TaskDiagnostics(**dict(task._mapping),
            is_unfinished=task.status not in TERMINAL_TASKS or task.recovery_required,
        ) if task.task_id is not None else None,
        session_work=SessionWorkDiagnostics(
            resources_active=row.resources_active, has_unfinished_work=unfinished,
            can_start_new_run=not reasons, blocking_reasons=reasons,
            execution=SessionExecutionDiagnostics(**details),
        ),
    )


async def read_diagnostics(db, session_id: UUID, run_id: UUID, *, user_id: UUID | None):
    queries = OWNER_QUERIES if user_id is not None else ADMIN_QUERIES
    row = await db.scalar(queries[1], _params(session_id, run_id, user_id))
    if row is None:
        raise HTTPException(404, "Run not found.")
    return _resource(row)


async def list_invocations(db, session_id: UUID, run_id: UUID, params, *, user_id: UUID | None):
    queries = OWNER_QUERIES if user_id is not None else ADMIN_QUERIES
    values = _params(session_id, run_id, user_id)
    # An existing taskless legacy Run returns its history; a missing Run is404.
    if (await db.execute(queries[0], values)).one_or_none() is None:
        raise HTTPException(404, "Run not found.")
    rows, page = await fetch_page(db, queries[2].params(**values), model=Run, id_name="run_id", params=params)
    items = []
    for row in rows:
        item = dict(row._mapping)
        item["run_id"] = item.pop("public_run_id")
        items.append(RunInvocationResource(**item))
    return {"items": items, "page": page}
