from uuid import UUID

import json
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_id
from app.core.database import get_db
from app.models.common.agent_run_model import AgentRunModel
from app.models.common.task_model import TaskModel
from app.repositories.session_repository import SessionRepository
from app.schemas.common.run_schema import RunCreate, RunResource
from app.schemas.common.task_schema import TaskCancel, TaskResource, TaskResume
from app.services.run_service import RunService
from app.services.task_event_service import TaskEventService
from config import settings
from app.core.database import get_session_factory
from app.services.task_service import TaskService


router = APIRouter(tags=["tasks"])


def _resource(task: TaskModel) -> TaskResource:
    return TaskResource(
        task_id=task.task_id,
        graph_task_id=task.graph_task_id,
        root_run_id=task.root_run_id,
        checkpoint_run_id=task.checkpoint_run_id,
        session_id=task.session_id,
        trigger_message_id=task.trigger_message_id,
        trigger_type=task.trigger_type,
        status=task.status,
        is_active=task.status in TaskService.ACTIVE_STATUSES,
        lock_owner=task.lock_owner,
        heartbeat_at=task.heartbeat_at,
        lease_expires_at=task.lease_expires_at,
        cancel_requested_at=task.cancel_requested_at,
        failure_reason=task.failure_reason,
        created_at=task.created_at,
        updated_at=task.updated_at,
        completed_at=task.completed_at,
    )


@router.get("/sessions/{session_id}/tasks", response_model=list[TaskResource])
async def list_session_tasks(
    session_id: UUID,
    limit: int = Query(default=50, ge=1, le=200),
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """E03 잠금 검증 화면에서 Session의 최신 Task와 Active lock을 관찰합니다."""

    session = await SessionRepository.get_active_by_user(
        db, user_id=user_id, session_id=session_id
    )
    if session is None:
        # 존재 여부와 타 사용자 소유 여부를 구분해 노출하지 않습니다.
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Session not found.")
    tasks = list((await db.scalars(
        select(TaskModel)
        .where(TaskModel.session_id == session_id)
        .order_by(TaskModel.created_at.desc(), TaskModel.task_id.desc())
        .limit(limit)
    )).all())
    return [_resource(task) for task in tasks]


async def _owned_task(db: AsyncSession, user_id: UUID, task_id: UUID) -> TaskModel:
    task = await db.scalar(select(TaskModel).where(TaskModel.task_id == task_id))
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    session = await SessionRepository.get_active_by_user(
        db, user_id=user_id, session_id=task.session_id
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return task


@router.get("/tasks/{task_id}", response_model=TaskResource)
async def read_task(
    task_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return _resource(await _owned_task(db, user_id, task_id))


@router.get("/tasks/{task_id}/runs", response_model=list[RunResource])
async def list_task_runs(
    task_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    await _owned_task(db, user_id, task_id)
    runs = list((await db.scalars(
        select(AgentRunModel)
        .where(AgentRunModel.task_id == task_id)
        .order_by(AgentRunModel.created_at)
    )).all())
    return [RunResource.model_validate(run) for run in runs]


@router.post("/tasks/{task_id}/resume", response_model=RunResource, status_code=status.HTTP_202_ACCEPTED)
async def resume_task(
    task_id: UUID,
    payload: TaskResume,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    if not idempotency_key or not idempotency_key.strip():
        raise HTTPException(status_code=400, detail="Idempotency-Key is required.")
    if len(idempotency_key) > 255:
        raise HTTPException(status_code=422, detail="Idempotency-Key must not exceed 255 characters.")
    task = await _owned_task(db, user_id, task_id)
    run = await RunService.create(
        db,
        user_id,
        task.session_id,
        RunCreate(
            command=payload.command,
            metadata={
                **payload.metadata,
                "resume_run_id": str(task.checkpoint_run_id),
                "task_id": str(task.task_id),
            },
        ),
        idempotency_key.strip(),
    )
    return RunResource.model_validate(run)


@router.post("/tasks/{task_id}/cancel", response_model=TaskResource, status_code=status.HTTP_202_ACCEPTED)
async def cancel_task(
    task_id: UUID,
    payload: TaskCancel,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return _resource(await RunService.cancel_task(db, user_id, task_id, payload.reason))


@router.get("/tasks/{task_id}/stream")
async def stream_task(
    request: Request,
    task_id: UUID,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    # StreamingResponse가 열린 동안 인증/소유권 확인용 DB session을 붙잡지 않는다.
    user_id: UUID = Depends(get_current_user_id, scope="function"),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Task의 모든 최초/Resume Run event를 하나의 sequence로 재생합니다."""
    await _owned_task(db, user_id, task_id)
    try:
        sequence = int(last_event_id or "0")
        if sequence < 0:
            raise ValueError
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Last-Event-ID must be a non-negative integer.") from exc

    async def generate():
        nonlocal sequence
        last_heartbeat = time.monotonic()
        while True:
            if await request.is_disconnected():
                return
            async with get_session_factory()() as event_db:
                events = await TaskEventService.list_after_task(
                    event_db, task_id=task_id, sequence=sequence,
                    limit=settings.sse_event_batch_size,
                )
                task_status = await event_db.scalar(
                    select(TaskModel.status).where(TaskModel.task_id == task_id)
                )
            for event in events:
                sequence = event.sequence
                data = json.dumps(event.payload, ensure_ascii=False, default=str, separators=(",", ":"))
                yield f"id: {event.sequence}\nevent: {event.event_type}\ndata: {data}\n\n"
            # FAQ/chitchat은 분석 Task를 제거한다. 이 경우에도 SSE를 닫아 화면이
            # 최신 AgentRun/Message를 DB에서 다시 읽을 수 있게 한다.
            if (task_status is None or task_status in TaskService.TERMINAL_STATUSES) and not events:
                return
            now = time.monotonic()
            if now - last_heartbeat >= settings.sse_heartbeat_seconds:
                yield ": heartbeat\n\n"
                last_heartbeat = now
            import asyncio
            await asyncio.sleep(settings.sse_poll_interval_seconds)

    return StreamingResponse(
        generate(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
