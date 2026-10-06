"""Immutable SQL for public Run state; every execution reads current DB rows.

Keep root/latest/Task in one statement snapshot. Scalar Bundles avoid refreshing
writable ORM objects. Only SQL structure is shared across calls and sessions;
identity, permissions, results and DB transactions are never cached here.
"""
from sqlalchemy import bindparam, cast, func, select
from sqlalchemy.dialects.postgresql import JSONPATH
from sqlalchemy.orm import Bundle, aliased

from api_service.models.agent_run_model import AgentRunModel as Run
from api_service.models.task_model import TaskModel as Task


def _build_projection(*, summary=False):
    root, latest = aliased(Run), aliased(Run)
    latest_id = (select(Run.run_id).where(Run.public_run_id == root.run_id)
                 .order_by(Run.created_at.desc(), Run.run_id.desc()).limit(1).correlate(root).scalar_subquery())
    # Both projections use the same root/latest/Task relationship. Summary
    # fetches no result, failure, interrupt body, checkpoint or attempt details.
    root_fields = [root.run_id, root.session_id, root.created_at, root.updated_at,
                   root.started_at, root.metadata_json["_model_selection"].label("model_selection")]
    latest_fields = [latest.run_id, latest.task_id, latest.status, latest.updated_at, latest.completed_at]
    task_fields = [Task.task_id, Task.status, Task.recovery_required, Task.updated_at, Task.completed_at]
    if summary:
        latest_fields.append(func.jsonb_path_exists(latest.interrupt,
            cast('$[*] ? (@.kind == "EXECUTOR_EVENT")', JSONPATH)).label("executor_wait"))
    else:
        root_fields.append(root.metadata_json["checkpoint_run_id"].label("checkpoint_run_id"))
        latest_fields.extend([latest.interrupt, latest.failure, latest.agent_response,
            latest.attempt_count, latest.next_attempt_at, latest.cancel_reason, latest.cancel_requested_at])
        task_fields.append(Task.cancel_requested_at)
    # Scalar bundles never populate/expire writable Run or Task ORM objects.
    statement = select(Bundle("root", *root_fields), Bundle("latest", *latest_fields),
                       Bundle("task", *task_fields),
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


_SUMMARY_BASE, _SUMMARY_ROOT = _build_projection(summary=True)
PUBLIC_RUN_SUMMARIES = _SUMMARY_BASE.where(
    _SUMMARY_ROOT.run_id.in_(bindparam("public_run_ids", expanding=True)),
)
