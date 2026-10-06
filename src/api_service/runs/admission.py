"""Admit pending Runs atomically with session locks and queued events."""

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.models.enums import AgentRunStatus, TaskStatus
from api_service.models.agent_run_model import AgentRunModel
from api_service.models.message_model import MessageModel
from api_service.models.task_model import TaskModel
from api_service.runs.errors import InvalidRunRequest, RunConflict
from api_service.runs.repository import interrupted_run
from api_service.runs.requests import request_digest, select_model, validate_model, validate_replay
from api_service.schemas.run_schema import RunCreate
from api_service.resources import lifecycle
from api_service.runs.task_events import TaskEventService
from api_service.runs.tasks import TaskService


async def enqueue(db: AsyncSession, user_id: UUID, session_id: UUID, payload: RunCreate, key: str) -> AgentRunModel:
    """Commit a pending invocation and its session lock; never execute a graph."""
    await lifecycle.lock_session(db, user_id, session_id)
    previous = await db.scalar(select(AgentRunModel).where(
        AgentRunModel.session_id == session_id, AgentRunModel.idempotency_key == key
    ))
    if previous is not None:
        validate_replay(previous, payload)
        return previous
    owner = "queue:unclaimed"
    origin: AgentRunModel | None = None
    try:
        from api_service.resources.session_activity import require_input
        if payload.command is None:
            await require_input(db, session_id)
            model_selection = select_model(payload.main_model_name)
            # Queue 대기 중에도 동일 Session의 두 분석 요청이 들어오지 못하게 Task를 선점합니다.
            task = TaskService.create_model(
                session_id=session_id, idempotency_key=key, owner=owner
            )
            task.status = TaskStatus.PENDING
            db.add(task)
            await db.flush()
        else:
            resume_id = payload.metadata.get("resume_run_id") or payload.metadata.get("checkpoint_run_id")
            origin = await interrupted_run(
                db, session_id, UUID(str(resume_id)) if resume_id else None
            )
            latest_id = await db.scalar(select(AgentRunModel.run_id).where(
                AgentRunModel.public_run_id == origin.public_run_id
            ).order_by(AgentRunModel.created_at.desc(), AgentRunModel.run_id.desc()).limit(1))
            if latest_id != origin.run_id:
                raise RunConflict("Resume target is no longer the current interrupt.")
            if any(item.get("kind") == "EXECUTOR_EVENT" for item in (origin.interrupt or []) if isinstance(item, dict)):
                raise RunConflict("Session is waiting for Executor; user resume is not allowed.")
            await require_input(db, session_id, resume_public_id=origin.public_run_id)
            root = await db.get(AgentRunModel, origin.public_run_id)
            model_selection = (root.metadata_json or {}).get("_model_selection")
            validate_model(model_selection)
            if origin.task_id is None:
                # FAQ 등 Task 없는 checkpoint 재개도 durable queue를 거칩니다.
                task = TaskService.create_model(
                    session_id=session_id, idempotency_key=key, owner=owner
                )
                task.status = TaskStatus.PENDING
                task.checkpoint_run_id = origin.checkpoint_run_id or origin.run_id
                db.add(task)
                await db.flush()
            else:
                task = await db.scalar(
                    select(TaskModel).where(TaskModel.task_id == origin.task_id).with_for_update()
                )
                if task is None or task.status != TaskStatus.WAITING_INPUT:
                    raise RunConflict("Task is not waiting for input.")
                if task.cancel_requested_at is not None:
                    raise RunConflict("Task cancellation has been requested.")
                # 재개 요청도 먼저 pending으로 원자 전환하고 Worker가 실제 lease를 획득합니다.
                task.status = TaskStatus.PENDING
                task.lock_owner = owner
                task.lock_token = None
                task.heartbeat_at = None
                task.lease_expires_at = None
    except IntegrityError as exc:
        await db.rollback()
        duplicate = await db.scalar(select(AgentRunModel).where(
            AgentRunModel.session_id == session_id, AgentRunModel.idempotency_key == key
        ))
        if duplicate is not None:
            validate_replay(duplicate, payload)
            return duplicate
        raise RunConflict("An active task already exists for this session.") from exc

    checkpoint_run_id = task.checkpoint_run_id or (origin.checkpoint_run_id if origin else None)
    metadata = dict(payload.metadata)
    # Internal recovery controls are never accepted from client metadata.
    for field in ("_resume_target", "_resume_started", "_checkpoint_interrupt_id", "_initial_protocol", "_initial_started"):
        metadata.pop(field, None)
    if origin is None:
        metadata["_initial_protocol"] = 1
        metadata["_initial_started"] = False
    if origin is not None:
        metadata["_resume_target"] = (origin.metadata_json or {}).get("_checkpoint_interrupt_id")
        metadata["_resume_started"] = False
    metadata["_model_selection"] = model_selection
    metadata["_request_digest"] = request_digest(payload)
    metadata["requested_by_user_id"] = str(user_id)
    if checkpoint_run_id is not None:
        metadata["checkpoint_run_id"] = str(checkpoint_run_id)
    invocation_id = uuid4()
    run = AgentRunModel(
        run_id=invocation_id,
        public_run_id=origin.public_run_id if origin else invocation_id,
        session_id=session_id, task_id=task.task_id,
        status=AgentRunStatus.PENDING,
        input_json=payload.input.model_dump(mode="json") if payload.input else None,
        command_json=payload.command, metadata_json=metadata,
        idempotency_key=key, multitask_strategy=payload.multitask_strategy,
        stream_mode=payload.stream_mode, stream_resumable=payload.stream_resumable,
        on_disconnect=payload.on_disconnect,
    )
    db.add(run)
    await db.flush()
    # 화면/API coordinator가 먼저 저장한 원본 User Message를 Run과 분석 Task에 연결한다.
    # Message CRUD는 자기 테이블만 처리하고, 관계 연결은 Run 접수 경계가 담당한다.
    trigger_message_id = metadata.get("trigger_message_id")
    if trigger_message_id is not None:
        try:
            trigger_message_uuid = UUID(str(trigger_message_id))
        except ValueError as exc:
            raise InvalidRunRequest("Invalid trigger_message_id.") from exc
        trigger_message = await db.scalar(
            select(MessageModel).where(
                MessageModel.message_id == trigger_message_uuid,
                MessageModel.session_id == session_id,
            )
        )
        if trigger_message is None:
            raise InvalidRunRequest("Trigger message does not belong to this session.")
        run.trigger_message_id = trigger_message_uuid
        task.trigger_message_id = trigger_message_uuid
    if task.root_run_id is None:
        task.root_run_id = run.public_run_id
    if task.checkpoint_run_id is None:
        task.checkpoint_run_id = run.run_id
        run.metadata_json = {**(run.metadata_json or {}), "checkpoint_run_id": str(run.run_id)}
    # Worker가 pending Run을 보기 전에 queued event까지 같은 transaction에서 완성한다.
    # commit 뒤 이벤트를 추가하면 Worker의 Task lease UPDATE와 lock 순서가 엇갈려
    # PostgreSQL deadlock이 발생할 수 있다.
    await TaskEventService.append(
        db,
        task_id=task.task_id,
        run_id=run.run_id,
        event_type="task.queued",
        payload={"status": AgentRunStatus.PENDING.value},
        commit=False,
    )
    from api_service.runs.commands.admission import enqueue_user
    await enqueue_user(db, run)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        duplicate = await db.scalar(select(AgentRunModel).where(
            AgentRunModel.session_id == session_id, AgentRunModel.idempotency_key == key
        ))
        if duplicate is not None:
            validate_replay(duplicate, payload)
            return duplicate
        raise RunConflict("An active task already exists for this session.") from exc
    await db.refresh(run)
    await db.refresh(task)
    return run
