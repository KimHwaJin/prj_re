"""Public lifecycle projection over durable, private invocation records.

No second state machine: one SQL snapshot combines the latest invocation and
Task. Workers still claim invocation IDs; checkpoints keep their original IDs.
"""
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Bundle, aliased
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.engine import Row

from api_service.core.enums import AgentRunStatus
from api_service.models.common.agent_run_model import AgentRunModel as Run
from api_service.models.common.task_model import TaskModel as Task
from api_service.schemas.common.run_schema import PublicRunResource, RunCreate, RunResume, RunStart, RunCancel
from api_service.services.run_service import RunService
from api_service.services.task_service import TaskService
from api_service.services.helpers import utc_now
from api_service.services import resource_lifecycle as lifecycle


TERMINAL = {"success", "error", "timeout", "canceled"}


def project(root: Row, latest: Row, task: Row | None) -> PublicRunResource:
    status = latest.status.value
    if task and task.status in TaskService.TERMINAL_STATUSES:
        status = task.status.value
    elif status == AgentRunStatus.INTERRUPTED:
        status = "waiting_executor" if any(
            item.get("kind") == "EXECUTOR_EVENT" for item in (latest.interrupt or [])
        ) else "waiting_input"
    recovery = bool(task and task.recovery_required)
    if recovery:
        status = "recovery_required"
    terminal = status in TERMINAL
    times = [root.updated_at, latest.updated_at] + ([task.updated_at] if task else [])
    return PublicRunResource(
        main_model_name=(root.model_selection or {}).get("name"),
        model_revision=(root.model_selection or {}).get("revision"),
        run_id=root.run_id, session_id=root.session_id, status=status,
        resume_token=latest.run_id if status == "waiting_input" and not (task and task.cancel_requested_at) else None,
        interrupt=latest.interrupt if status in {"waiting_input", "waiting_executor"} else None,
        failure=latest.failure, result=latest.agent_response if terminal else None,
        recovery_required=recovery, checkpoint_run_id=UUID(str(root.checkpoint_run_id)) if root.checkpoint_run_id else root.run_id,
        task_id=latest.task_id, attempt_count=latest.attempt_count,
        next_attempt_at=latest.next_attempt_at, cancel_reason=latest.cancel_reason,
        cancel_requested_at=task.cancel_requested_at if task else latest.cancel_requested_at,
        created_at=root.created_at, updated_at=max(times), started_at=root.started_at,
        completed_at=(task.completed_at if task else latest.completed_at) if terminal else None,
    )


class PublicRunService:
    @staticmethod
    async def admission_lock(db: AsyncSession, user_id: UUID, session_id: UUID) -> None:
        await lifecycle.lock_session(db, user_id, session_id)

    @staticmethod
    def _snapshot_query():
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

    @staticmethod
    async def snapshots(db: AsyncSession, ids: list[UUID]):
        if not ids:
            return {}
        statement, root = PublicRunService._snapshot_query()
        rows = await db.execute(statement.where(root.run_id.in_(ids)))
        return {r.run_id: (r, invocation, task if task.task_id is not None else None)
                for r, invocation, task in rows}

    @staticmethod
    async def read(db: AsyncSession, user_id: UUID, session_id: UUID, run_id: UUID) -> PublicRunResource:
        await RunService._session(db, user_id, session_id)
        # Resolve legacy invocation aliases in the same statement snapshot.
        canonical = select(Run.public_run_id).where(
            Run.run_id == run_id, Run.session_id == session_id,
        ).scalar_subquery()
        statement, root = PublicRunService._snapshot_query()
        row = (await db.execute(statement.where(
            root.run_id == canonical, root.session_id == session_id,
        ))).one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        r, invocation, task = row
        return project(r, invocation, task if task.task_id is not None else None)

    @staticmethod
    async def create(db: AsyncSession, user_id: UUID, session_id: UUID, payload: RunStart, key: str) -> PublicRunResource:
        payload = RunCreate(**payload.model_dump())
        reserved = {"resume_run_id", "checkpoint_run_id", "task_id", "_request_digest", "_public_resume", "_model_selection", "_resume_target", "_resume_started", "_checkpoint_interrupt_id", "_initial_protocol", "_initial_started"}
        if reserved.intersection(payload.metadata) or any(k.startswith('_') for k in payload.metadata):
            raise HTTPException(status_code=422, detail="Execution identity metadata is managed by the server.")
        invocation = await RunService.create(db, user_id, session_id, payload, key)
        return await PublicRunService.read(db, user_id, session_id, invocation.public_run_id)

    @staticmethod
    async def resume(db: AsyncSession, user_id: UUID, session_id: UUID, run_id: UUID, payload: RunResume, key: str) -> PublicRunResource:
        await RunService._session(db, user_id, session_id)
        await PublicRunService.admission_lock(db, user_id, session_id)
        current = await PublicRunService.read(db, user_id, session_id, run_id)
        command = RunCreate(command=payload.command, metadata={
            "resume_run_id": str(payload.resume_token), "_public_resume": str(current.run_id),
        })
        previous = await db.scalar(select(Run).where(Run.session_id == session_id, Run.idempotency_key == key))
        if previous is not None:
            if previous.public_run_id != current.run_id:
                raise HTTPException(status_code=409, detail="Idempotency-Key belongs to another Run.")
            RunService.validate_replay(previous, command)
            return current
        if current.status != "waiting_input" or current.resume_token != payload.resume_token:
            raise HTTPException(status_code=409, detail="Run is not waiting for this input; refresh Run state.")
        latest = await db.get(Run, payload.resume_token)
        if (latest.metadata_json or {}).get('_agent_runtime') == 'agentic-planning-v1':
            repair_review=latest.metadata_json.get('_repair_review')
            if repair_review:
                from service_contracts.execution_repair import validate_repair_action
                action=(payload.command or {}).get('resume')
                if (action or {}).get('revision')!=repair_review['revision']:
                    raise HTTPException(409,'Stale repair revision; refresh Run state.')
                try:
                    validate_repair_action(repair_review,action)
                except (ValueError,TypeError) as exc:
                    raise HTTPException(422,str(exc)) from exc
                await RunService.create(db,user_id,session_id,command,key)
                return await PublicRunService.read(db,user_id,session_id,current.run_id)
            decision_review=latest.metadata_json.get('_decision_review')
            if decision_review:
                from service_contracts.execution_review import validate_decision_action
                action=(payload.command or {}).get('resume')
                if (action or {}).get('revision') != decision_review['revision']:
                    raise HTTPException(409,'Stale decision revision; refresh Run state.')
                try:
                    validate_decision_action(decision_review,action)
                except (ValueError,TypeError) as exc:
                    raise HTTPException(422,str(exc)) from exc
                await RunService.create(db,user_id,session_id,command,key)
                return await PublicRunService.read(db,user_id,session_id,current.run_id)
            revision_action=(payload.command or {}).get('resume')
            if isinstance(revision_action,dict) and revision_action.get('action') in {'replan','answer_clarification'}:
                from service_contracts.plan_interaction import validate_plan_revision
                from service_settings import get_settings
                interaction=latest.metadata_json.get('_planning_interaction') or {}
                if revision_action.get('revision')!=interaction.get('revision') or revision_action.get('interaction_id')!=interaction.get('interaction_id'):
                    raise HTTPException(409,'Stale planning interaction; refresh Run state.')
                try:
                    validate_plan_revision(interaction,revision_action,count=latest.metadata_json.get('_planning_revision_count',0),
                        limit=get_settings().agent.agent_max_plan_revisions)
                except (ValueError,TypeError) as exc:
                    raise HTTPException(422,str(exc)) from exc
                await RunService.create(db,user_id,session_id,command,key)
                return await PublicRunService.read(db,user_id,session_id,current.run_id)
            from service_contracts.plan_review import patch_review
            from service_settings import get_settings
            session = await RunService._session(db, user_id, session_id)
            action = (payload.command or {}).get('resume') if isinstance(payload.command, dict) else None
            reviews = latest.metadata_json.get('_plan_reviews', [])
            selected = next((r for r in reviews if r['plan_id'] == (action or {}).get('plan_id')), None)
            if selected is None:
                raise HTTPException(422, 'Unknown plan.')
            if (action or {}).get('plan_revision') != selected['plan_revision']:
                raise HTTPException(409, 'Stale plan revision; refresh Run state.')
            try:
                patch_review(selected, action, datasets=get_settings().agent.analysis_datasets,
                    context={'user_id': str(user_id), 'project_id': str(session.project_id), 'session_id': str(session_id)})
            except (ValueError, TypeError) as exc:
                raise HTTPException(422, str(exc)) from exc
        await RunService.create(db, user_id, session_id, command, key)
        return await PublicRunService.read(db, user_id, session_id, current.run_id)

    @staticmethod
    async def cancel(db: AsyncSession, user_id: UUID, session_id: UUID, run_id: UUID, payload: RunCancel) -> PublicRunResource:
        await RunService._session(db, user_id, session_id)
        await PublicRunService.admission_lock(db, user_id, session_id)
        current = await PublicRunService.read(db, user_id, session_id, run_id)
        if current.status in TERMINAL:
            if current.status == "canceled":
                return current
            raise HTTPException(status_code=409, detail="Run is already terminal.")
        if current.status == "waiting_executor":
            # We have no confirmed remote cancellation protocol here. Never
            # unlock a week-long external job by merely canceling the local Task.
            raise HTTPException(status_code=409, detail="Executor cancellation is not supported; the Run remains locked until its result.")
        if current.task_id:
            await RunService.cancel_task(db, user_id, current.task_id, payload.reason)
        else:
            snapshots = await PublicRunService.snapshots(db, [current.run_id])
            latest = await db.scalar(select(Run).where(Run.run_id == snapshots[current.run_id][1].run_id)
                                     .with_for_update().execution_options(populate_existing=True))
            if latest.status != AgentRunStatus.INTERRUPTED:
                raise HTTPException(status_code=409, detail="Legacy Run has no Task; execution termination requires recovery.")
            # Historical FAQ interrupts have no Worker to consume a cancel flag.
            # The paused invocation is finished, so close only its public history.
            latest.cancel_requested_at = utc_now()
            latest.cancel_reason = payload.reason
            RunService._finish_run(latest, AgentRunStatus.CANCELED)
            await db.commit()
        return await PublicRunService.read(db, user_id, session_id, current.run_id)
