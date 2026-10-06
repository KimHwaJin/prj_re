from dtest.application.runs.queries import list_runs as list_session_runs
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from dtest.api_service.http.dependencies import get_current_user_id, get_stream_user_id
from dtest.infrastructure.database.runtime import get_db
from dtest.contracts.pagination import ListParams
from dtest.api_service.http.pagination import list_params
from dtest.contracts.resources.api_schema import Page
from dtest.contracts.resources.run_schema import (
    AgentRunLogResource,
    RunCancel,
    RunStart,
    PublicRunResource,
    PublicRunSummary,
    RunResume,
)
from dtest.contracts.run_request import RunRequest
from dtest.application.runs.service import PublicRunService
from dtest.application.runs.log_queries import list_diagnostic_logs
from dtest.settings.api import settings
from dtest.infrastructure.database.runtime import get_session_factory

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
    response.headers["Location"] = f"/api/v1/sessions/{session_id}/runs/{run.run_id}"
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
    from dtest.api_service.streaming import RunStreamResponse
    response = RunStreamResponse(request.app.state.run_stream_hub, request,
        (user_id, session_id, run.run_id), sequence)
    response.headers['Location'] = f'/api/v1/sessions/{session_id}/runs/{run.run_id}'
    response.headers['X-Run-Id'] = str(run.run_id)
    return response


def parse_sequence(value):
    try:
        result = int(value or '0')
        if result < 0:
            raise ValueError
        return result
    except ValueError as exc:
        raise HTTPException(400, 'Last-Event-ID must be a non-negative integer.') from exc


@router.get("/sessions/{session_id}/runs", response_model=Page[PublicRunSummary])
async def list_runs(session_id: UUID, params: ListParams = Depends(list_params), user_id: UUID = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    return await list_session_runs(db, user_id, session_id, params)


@router.get("/sessions/{session_id}/runs/{run_id}", response_model=PublicRunResource)
async def read_run(session_id: UUID, run_id: UUID, user_id: UUID = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    return await PublicRunService.read(db, user_id, session_id, run_id)


@router.get(
    "/sessions/{session_id}/runs/{run_id}/logs",
    response_model=Page[AgentRunLogResource],
    summary="Agent 실행 진단 로그 조회",
)
async def list_run_logs(
    session_id: UUID,
    run_id: UUID,
    params: ListParams = Depends(list_params),
    agent_name: str | None = Query(default=None, min_length=1, max_length=100, description="Agent 이름 정확 일치"),
    node: str | None = Query(default=None, min_length=1, max_length=100, description="그래프 노드 이름 정확 일치"),
    event: str | None = Query(default=None, min_length=1, max_length=100, description="기록된 이벤트 이름 정확 일치"),
    kind: str | None = Query(default=None, min_length=1, max_length=50, description="로그 분류 정확 일치"),
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """소유한 Run의 구조화된 진단 기록을 페이지로 조회합니다.

    프론트 진행/HITL/재접속은 SSE를 사용합니다. payload는 기록 종류별로
    다르며, 로그 저장 시각 순서는 SSE sequence나 실행의 인과 순서가 아닙니다.
    """
    return await list_diagnostic_logs(
        db, user_id=user_id, session_id=session_id, run_id=run_id, params=params,
        agent_name=agent_name, node=node, event=event, kind=kind,
    )


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

    from dtest.api_service.streaming import RunStreamHub, RunStreamResponse
    application = getattr(request, 'app', None)
    owned = application is None  # Direct adapter tests/development, outside ASGI.
    hub = RunStreamHub(settings, session_factory=lambda: get_session_factory()()) if owned else application.state.run_stream_hub

    if not owned:
        return RunStreamResponse(hub, request, (user_id, session_id, public.run_id), initial_sequence)

    async def event_generator():
        try:
            async with hub.subscribe(user_id, session_id, public.run_id) as entry:
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
