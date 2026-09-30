from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Bundle
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.core.auth import get_current_user_id, get_stream_user_id
from api_service.core.database import get_db
from api_service.core.pagination import ListParams, fetch_page, list_params
from api_service.models import AgentRunLogModel, AgentRunModel
from api_service.schemas.common.api_schema import Page
from api_service.schemas.common.run_schema import (
    AgentRunLogResource,
    RunCancel,
    RunStart,
    PublicRunResource,
    RunResume,
)
from service_contracts.run_request import RunRequest
from api_service.services.run_service import RunService
from api_service.services.public_run_service import PublicRunService, project
from config import settings
from api_service.core.database import get_session_factory

router = APIRouter(tags=["runs"])


@router.post("/sessions/{session_id}/runs", response_model=PublicRunResource, status_code=status.HTTP_202_ACCEPTED)
async def create_run(
    session_id: UUID, payload: RunRequest, response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user_id: UUID = Depends(get_current_user_id), db: AsyncSession = Depends(get_db),
):
    if not idempotency_key or not idempotency_key.strip():
        raise HTTPException(status_code=400, detail="Idempotency-Key is required.")
    if len(idempotency_key) > 255:
        raise HTTPException(status_code=422, detail="Idempotency-Key must not exceed 255 characters.")
    run = await submit_request(db, user_id, session_id, payload, idempotency_key)
    response.headers["Location"] = f"/api/v1/sessions/{session_id}/runs/{run.id}"
    return run


async def submit_request(db, user_id, session_id, payload, key):
    if not key or not key.strip():
        raise HTTPException(400, 'Idempotency-Key is required.')
    if len(key) > 255:
        raise HTTPException(422, 'Idempotency-Key must not exceed 255 characters.')
    if payload.command is not None:
        return await PublicRunService.resume(db, user_id, session_id, payload.run_id,
            RunResume(command=payload.command.model_dump(mode='json', exclude_unset=True), resume_token=payload.resume_token), key)
    if any(part.type != 'text' for part in payload.input.content):
        raise HTTPException(422, 'Image/file input requires the future authorized attachment service; text input is supported now.')
    text = '\n'.join(part.text for part in payload.input.content).strip()
    if not text:
        raise HTTPException(422, 'A non-blank user message is required.')
    return await PublicRunService.create(db, user_id, session_id,
        RunStart(input={'messages': [{'role': 'user', 'content': text}]}, main_model_name=payload.main_model_name), key)


@router.post('/sessions/{session_id}/runs/stream')
async def create_run_stream(
    request: Request, session_id: UUID, payload: RunRequest,
    idempotency_key: str | None = Header(default=None, alias='Idempotency-Key'),
    last_event_id: str | None = Header(default=None, alias='Last-Event-ID'),
    user_id: UUID = Depends(get_stream_user_id, scope='function'),
    db: AsyncSession = Depends(get_db, scope='function'),
):
    # Validate the cursor before enqueueing; a malformed HTTP request has no side effects.
    sequence = parse_sequence(last_event_id)
    run = await submit_request(db, user_id, session_id, payload, idempotency_key)
    from api_service.services.run_stream_service import RunStreamResponse
    response = RunStreamResponse(request.app.state.run_stream_hub, request,
        (user_id, session_id, run.id), sequence)
    response.headers['Location'] = f'/api/v1/sessions/{session_id}/runs/{run.id}'
    response.headers['X-Run-Id'] = str(run.id)
    return response


def parse_sequence(value):
    try:
        result = int(value or '0')
        if result < 0:
            raise ValueError
        return result
    except ValueError as exc:
        raise HTTPException(400, 'Last-Event-ID must be a non-negative integer.') from exc


@router.get("/sessions/{session_id}/runs", response_model=Page[PublicRunResource])
async def list_runs(session_id: UUID, params: ListParams = Depends(list_params), user_id: UUID = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    await RunService._session(db, user_id, session_id)
    # Page only IDs/timestamps; hydrate the selected public states in one query.
    items, page = await fetch_page(
        db, select(Bundle("run_page", AgentRunModel.run_id, AgentRunModel.created_at)).where(
            AgentRunModel.session_id == session_id,
            AgentRunModel.public_run_id == AgentRunModel.run_id,
        ), model=AgentRunModel, id_name="run_id", params=params,
    )
    snapshots = await PublicRunService.snapshots(db, [item.run_id for item in items])
    return {"items": [project(*snapshots[item.run_id]) for item in items], "page": page}


@router.get("/sessions/{session_id}/runs/{run_id}", response_model=PublicRunResource)
async def read_run(session_id: UUID, run_id: UUID, user_id: UUID = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    return await PublicRunService.read(db, user_id, session_id, run_id)


@router.get(
    "/sessions/{session_id}/runs/{run_id}/logs",
    response_model=list[AgentRunLogResource],
)
async def list_run_logs(
    session_id: UUID,
    run_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """소유권을 확인한 뒤 Agent별 append-only 실행 로그를 반환합니다."""

    public = await PublicRunService.read(db, user_id, session_id, run_id)
    logs = (
        await db.scalars(
            select(AgentRunLogModel)
            .join(AgentRunModel, AgentRunModel.run_id == AgentRunLogModel.run_id)
            .where(AgentRunModel.public_run_id == public.id)
            .order_by(AgentRunLogModel.created_at, AgentRunLogModel.log_id)
        )
    ).all()
    return [AgentRunLogResource.model_validate(log).model_copy(update={"run_id": public.id}) for log in logs]


@router.get("/sessions/{session_id}/runs/{run_id}/join", response_model=PublicRunResource)
async def join_run(session_id: UUID, run_id: UUID, user_id: UUID = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    return await PublicRunService.read(db, user_id, session_id, run_id)


@router.post("/sessions/{session_id}/runs/{run_id}/cancel", response_model=PublicRunResource, status_code=status.HTTP_202_ACCEPTED)
async def cancel_run(session_id: UUID, run_id: UUID, payload: RunCancel, user_id: UUID = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    return await PublicRunService.cancel(db, user_id, session_id, run_id, payload)


@router.get("/sessions/{session_id}/runs/{run_id}/stream")
async def stream_run(
    request: Request,
    session_id: UUID,
    run_id: UUID,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    # StreamingResponse가 열린 동안 인증/소유권 확인용 DB session을 붙잡지 않는다.
    user_id: UUID = Depends(get_stream_user_id, scope="function"),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Replay durable events after Last-Event-ID, then wait for commit notifications."""

    public = await PublicRunService.read(db, user_id, session_id, run_id)
    initial_sequence = parse_sequence(last_event_id)

    from api_service.services.run_stream_service import RunStreamHub, RunStreamResponse
    application = getattr(request, 'app', None)
    owned = application is None  # Direct adapter tests/development, outside ASGI.
    hub = RunStreamHub(settings, session_factory=lambda: get_session_factory()()) if owned else application.state.run_stream_hub

    if not owned:
        return RunStreamResponse(hub, request, (user_id, session_id, public.id), initial_sequence)

    async def event_generator():
        try:
            async with hub.subscribe(user_id, session_id, public.id) as entry:
                async for chunk in hub.stream(request, entry, initial_sequence):
                    yield chunk
        finally:
            if owned:
                await hub.close()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
