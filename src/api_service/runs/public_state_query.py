"""Immutable SQL for public Run state; every execution reads current DB rows.

Keep root/latest/Task in one statement snapshot. Scalar Bundles avoid refreshing
writable ORM objects. Only SQL structure is shared across calls and sessions;
identity, permissions, results and DB transactions are never cached here.
"""
from sqlalchemy import bindparam, select
from sqlalchemy.orm import Bundle, aliased

from api_service.models.common.agent_run_model import AgentRunModel as Run
from api_service.models.common.task_model import TaskModel as Task


def _build_projection():
    root, latest = aliased(Run), aliased(Run)
    latest_id = (select(Run.run_id).where(Run.public_run_id == root.run_id)
                 .order_by(Run.created_at.desc(), Run.run_id.desc()).limit(1).correlate(root).scalar_subquery())
    # Scalar bundles never populate/expire writable Run or Task ORM objects.
    # Fetch only checkpoint/model references from the root metadata JSON.
    statement = select(
        Bundle("root", root.run_id, root.session_id, root.created_at,
               root.updated_at, root.started_at,
               root.metadata_json["checkpoint_run_id"].label("checkpoint_run_id"),
               root.metadata_json["_model_selection"].label("model_selection")),
        Bundle("latest", latest.run_id, latest.task_id, latest.status,
               latest.interrupt, latest.failure, latest.agent_response,
               latest.attempt_count, latest.next_attempt_at, latest.cancel_reason,
               latest.cancel_requested_at, latest.updated_at, latest.completed_at),
        Bundle("task", Task.task_id, Task.status, Task.recovery_required,
               Task.cancel_requested_at, Task.updated_at, Task.completed_at),
    ).select_from(root).join(latest, latest.run_id == latest_id).outerjoin(
        Task, Task.task_id == latest.task_id,
    ).where(root.public_run_id == root.run_id)
    return statement, root


_BASE, _ROOT = _build_projection()

# One SQL structure handles changing list sizes; each call binds fresh IDs.
PUBLIC_RUN_SNAPSHOTS = _BASE.where(
    _ROOT.run_id.in_(bindparam("public_run_ids", expanding=True)),
)

# Both legacy invocation IDs and canonical public IDs resolve in the same query.
_CANONICAL = select(Run.public_run_id).where(
    Run.run_id == bindparam("requested_run_id"),
    Run.session_id == bindparam("requested_session_id"),
).scalar_subquery()
PUBLIC_RUN_READ = _BASE.where(
    _ROOT.run_id == _CANONICAL,
    _ROOT.session_id == bindparam("requested_session_id"),
)
