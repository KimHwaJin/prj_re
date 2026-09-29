"""Read projections only: no claims, heartbeat refresh, expiry or recovery writes."""
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Bundle, aliased

from app.core.enums import DeleteYN
from app.core.pagination import fetch_page
from app.models.common.agent_run_model import AgentRunModel as Run
from app.models.common.project_model import ProjectModel as Project
from app.models.common.session_execution_model import SessionExecutionModel as Owner
from app.models.common.session_model import SessionModel as Session
from app.models.common.task_model import TaskModel as Task
from app.models.common.user_model import UserModel as User
from app.schemas.common.task_schema import TaskInvocationResource, TaskResource, SessionExecutionResource, SessionWorkResource
from app.services.resource_lifecycle import TERMINAL_TASKS, unfinished_work_conditions


# Token and idempotency keys are intentionally absent from every projection.
TASK_FIELDS = (
    "task_id", "graph_task_id", "root_run_id", "checkpoint_run_id", "session_id",
    "trigger_message_id", "trigger_type", "status", "lock_owner", "heartbeat_at",
    "lease_expires_at", "cancel_requested_at", "failure_reason", "recovery_required",
    "created_at", "updated_at", "completed_at",
)
INVOCATION_FIELDS = (
    "public_run_id", "task_id", "session_id", "status", "attempt_count",
    "next_attempt_at", "cancel_requested_at", "cancel_reason", "failure",
    "created_at", "updated_at", "started_at", "completed_at",
)


def _resources_active():
    return and_(Session.delete_yn == DeleteYN.N, Project.delete_yn == DeleteYN.N,
                User.delete_yn == DeleteYN.N)


def _scope(statement, user_id: UUID | None):
    # None is reserved for the separately authenticated administrator routes.
    if user_id is not None:
        statement = statement.where(Session.user_id == user_id, _resources_active())
    return statement


def _query(user_id: UUID | None):
    root = aliased(Run)
    blockers = unfinished_work_conditions(session_id=Session.session_id)
    statement = select(Bundle("diagnostic",
        Task.task_id, Task.created_at,
        Bundle("task", *(getattr(Task, field) for field in TASK_FIELDS)),
        root.public_run_id.label("public_run_id"),
        _resources_active().label("resources_active"),
        func.statement_timestamp().label("observed_at"),
        Bundle("blockers", *(condition.label(name) for name, condition in blockers.items())),
        Bundle("execution", Owner.token.is_not(None).label("ownership_held"),
               Owner.owner_kind, Owner.owner_id, Owner.owner_process,
               Owner.acquired_at, Owner.heartbeat_at,
               Owner.recovery_required, Owner.recovery_reason),
    )).select_from(Task).join(Session, Session.session_id == Task.session_id).join(
        Project, Project.project_id == Session.project_id,
    ).join(User, User.user_id == Session.user_id).outerjoin(
        root, root.run_id == Task.root_run_id,
    ).outerjoin(Owner, Owner.session_id == Session.session_id)
    return _scope(statement, user_id)


def _resource(row):
    task = row.task
    execution = row.execution
    owned, recovery = execution.ownership_held, bool(execution.recovery_required)
    details = dict(execution._mapping) if owned or recovery else {}
    details.update(ownership_held=owned, recovery_required=recovery)
    reasons = [name for name, blocked in row.blockers._mapping.items() if blocked]
    unfinished = bool(reasons)
    if not row.resources_active:
        reasons.insert(0, "resources_inactive")
    return TaskResource(
        **dict(task._mapping), public_run_id=row.public_run_id,
        is_unfinished=task.status not in TERMINAL_TASKS or task.recovery_required,
        observed_at=row.observed_at,
        session_work=SessionWorkResource(
            resources_active=row.resources_active, has_unfinished_work=unfinished,
            can_start_new_run=not reasons, blocking_reasons=reasons,
            execution=SessionExecutionResource(**details),
        ),
    )


async def read_task(db, task_id: UUID, *, user_id: UUID | None):
    row = await db.scalar(_query(user_id).where(Task.task_id == task_id))
    if row is None:
        raise HTTPException(404, "Task not found.")
    return _resource(row)


async def list_tasks(db, session_id: UUID, params, *, user_id: UUID | None):
    # Distinguish an empty session from a missing/inaccessible session.
    session = select(Session.session_id).join(Project, Project.project_id == Session.project_id).join(
        User, User.user_id == Session.user_id,
    ).where(Session.session_id == session_id)
    if await db.scalar(_scope(session, user_id)) is None:
        raise HTTPException(404, "Session not found.")
    rows, page = await fetch_page(db, _query(user_id).where(Task.session_id == session_id),
                                 model=Task, id_name="task_id", params=params)
    return {"items": [_resource(row) for row in rows], "page": page}


async def list_invocations(db, task_id: UUID, params, *, user_id: UUID | None):
    await read_task(db, task_id, user_id=user_id)
    # Reapply ownership/visibility in the page statement as well.
    statement = select(Bundle("invocation", Run.run_id.label("invocation_id"),
        Run.run_id, *(getattr(Run, field) for field in INVOCATION_FIELDS),
    )).join(Task, Task.task_id == Run.task_id).join(Session, Session.session_id == Task.session_id).join(
        Project, Project.project_id == Session.project_id,
    ).join(User, User.user_id == Session.user_id).where(Run.task_id == task_id)
    rows, page = await fetch_page(db, _scope(statement, user_id), model=Run, id_name="run_id", params=params)
    return {"items": [TaskInvocationResource(**dict(row._mapping)) for row in rows], "page": page}
