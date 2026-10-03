"""DB is the command/ownership authority; expiry never steals a live writer."""
import socket
from uuid import UUID, uuid4

from sqlalchemy import bindparam, or_, select
from sqlalchemy.orm import aliased

from api_service.core.enums import AgentRunStatus, TaskStatus
from api_service.core.execution_claim import ExecutionClaim
from api_service.models.common.agent_command_model import AgentCommandModel as Command
from api_service.models.common.agent_run_model import AgentRunModel as Run
from api_service.models.common.session_execution_model import SessionExecutionModel as Owner
from api_service.models.common.task_model import TaskModel as Task
from api_service.runs.commands.types import ClaimedEvent, ClaimedRun
from api_service.schemas.common.run_schema import RunCreate
from api_service.services.helpers import utc_now
from api_service.services.session_execution import SessionExecution, acquire
from api_service.services.task_service import TaskService
from service_contracts.events import EventContext, ExecutorEvent


def _claim_statement():
    """Build the immutable query once; rows and eligibility are always read from DB."""
    earlier = aliased(Command)
    blocked_session = select(Owner.session_id).where(
        Owner.session_id == Command.session_id,
        or_(Owner.token.is_not(None), Owner.recovery_required.is_(True)),
    ).exists()
    busy_task = select(Task.task_id).where(
        Task.session_id == Command.session_id,
        or_(Task.status.in_((TaskStatus.PENDING, TaskStatus.RUNNING)), Task.recovery_required.is_(True)),
    ).exists()
    predecessor = select(earlier.command_id).where(
        earlier.session_id == Command.session_id, earlier.ordinal < Command.ordinal,
        earlier.state.not_in(("DONE", "IGNORED", "FAILED")),
    ).exists()
    return select(Command).where(
        Command.namespace == bindparam("claim_namespace"), Command.state == "READY",
        Command.available_at <= bindparam("claim_now"), ~blocked_session, ~predecessor,
        or_(Command.kind != "executor_resume", ~busy_task),
    ).order_by(Command.ordinal).limit(1).with_for_update(skip_locked=True)


# This is SQL structure, not cached results, ownership or a shared DB session.
_CLAIM_STATEMENT = _claim_statement()


async def claim_one(factory, namespace):
    async with factory() as db:
        command = await db.scalar(_CLAIM_STATEMENT, {
            "claim_namespace": namespace, "claim_now": utc_now(),
        })
        if command is None:
            return None
        if command.kind == "executor_resume":
            payload = command.payload
            context = EventContext(namespace=namespace, session_id=str(command.session_id),
                task_id=payload["task_id"], execution_id=UUID(payload["execution_id"]),
                command_id=command.command_id, event=ExecutorEvent.model_validate(payload["event"]))
            owner = SessionExecution(command.session_id, uuid4(), command.command_id, "executor_event")
            item = ClaimedEvent(context, owner)
        else:
            run = await db.scalar(select(Run).where(Run.run_id == command.invocation_id).with_for_update())
            task = await db.scalar(select(Task).where(Task.task_id == run.task_id).with_for_update())
            if run.status not in (AgentRunStatus.PENDING, AgentRunStatus.RUNNING):
                command.state = "DONE"
                await db.commit()
                return None
            if run.status != AgentRunStatus.PENDING or task is None or task.status != TaskStatus.PENDING or task.recovery_required:
                command.state, command.last_error = "RECOVERY", "Invocation/task is not safely claimable"
                await db.commit()
                return None
            TaskService.start_execution(task, owner=f"gaia:{socket.gethostname()}")
            run.status, run.next_attempt_at, run.started_at = AgentRunStatus.RUNNING, None, utc_now()
            run.attempt_count += 1
            claim = ExecutionClaim(run.run_id, task.task_id, task.lock_token, run.attempt_count)
            owner = SessionExecution(run.session_id, claim.lock_token, run.run_id, "api_run")
            metadata = dict(run.metadata_json or {})
            item = ClaimedRun(claim, UUID(metadata["requested_by_user_id"]), run.session_id,
                RunCreate(input=run.input_json, command=run.command_json, metadata=metadata,
                    multitask_strategy=run.multitask_strategy, stream_mode=run.stream_mode,
                    stream_resumable=run.stream_resumable, on_disconnect=run.on_disconnect),
                run.idempotency_key, namespace)
        if not await acquire(db, owner):
            await db.rollback()
            return None
        command.state, command.owner_token = "RUNNING", owner.token
        command.attempt += 1
        command.updated_at = utc_now()
        await db.commit()
        return item
