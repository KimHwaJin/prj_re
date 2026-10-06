from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import StreamingResponse

from dtest.api_service.http.dependencies import (
    CurrentUserId,
    DBSession,
    StreamDBSession,
    StreamUserId,
)
from dtest.api_service.http.pagination import ListQuery
from dtest.application.runs.log_queries import list_diagnostic_logs
from dtest.application.runs.queries import list_runs as list_session_runs
from dtest.application.runs.service import PublicRunService
from dtest.application.runs.submission import submit_request
from dtest.contracts.resources.api_schema import Page
from dtest.contracts.resources.run_schema import (
    AgentRunLogResource,
    PublicRunResource,
    PublicRunSummary,
    RunCancel,
)
from dtest.contracts.run_request import RunRequest
from dtest.infrastructure.database.runtime import get_session_factory
from dtest.settings.api import settings

router = APIRouter(tags=["runs"])


@router.post(
    "/sessions/{session_id}/runs",
    response_model=PublicRunResource,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_run(
    session_id: UUID,
    payload: RunRequest,
    response: Response,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
    *,
    user_id: CurrentUserId,
    db: DBSession,
):
    run = await submit_request(
        db, user_id, session_id, payload, idempotency_key
    )
    response.headers["Location"] = (
        f"/api/v1/sessions/{session_id}/runs/{run.run_id}"
    )
    return run


@router.post("/sessions/{session_id}/runs/stream")
async def create_run_stream(
    request: Request,
    session_id: UUID,
    payload: RunRequest,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    *,
    user_id: StreamUserId,
    db: StreamDBSession,
):
    sequence = parse_sequence(last_event_id)
    run = await submit_request(
        db, user_id, session_id, payload, idempotency_key
    )
    from dtest.api_service.streaming import RunStreamResponse

    response = RunStreamResponse(
        request.app.state.run_stream_hub,
        request,
        (user_id, session_id, run.run_id),
        sequence,
    )
    response.headers["Location"] = (
        f"/api/v1/sessions/{session_id}/runs/{run.run_id}"
    )
    response.headers["X-Run-Id"] = str(run.run_id)
    return response


def parse_sequence(value):
    try:
        result = int(value or "0")
        if result < 0:
            raise ValueError
        return result
    except ValueError as exc:
        raise HTTPException(
            400, "Last-Event-ID must be a non-negative integer."
        ) from exc


@router.get(
    "/sessions/{session_id}/runs", response_model=Page[PublicRunSummary]
)
async def list_runs(
    session_id: UUID,
    params: ListQuery,
    user_id: CurrentUserId,
    db: DBSession,
):
    return await list_session_runs(db, user_id, session_id, params)


@router.get(
    "/sessions/{session_id}/runs/{run_id}", response_model=PublicRunResource
)
async def read_run(
    session_id: UUID,
    run_id: UUID,
    user_id: CurrentUserId,
    db: DBSession,
):
    return await PublicRunService.read(db, user_id, session_id, run_id)


@router.get(
    "/sessions/{session_id}/runs/{run_id}/logs",
    response_model=Page[AgentRunLogResource],
    summary="Agent 실행 진단 로그 조회",
)
async def list_run_logs(
    session_id: UUID,
    run_id: UUID,
    params: ListQuery,
    agent_name: Annotated[
        str | None,
        Query(
            min_length=1, max_length=100, description="Agent 이름 정확 일치"
        ),
    ] = None,
    node: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=100,
            description="그래프 노드 이름 정확 일치",
        ),
    ] = None,
    event: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=100,
            description="기록된 이벤트 이름 정확 일치",
        ),
    ] = None,
    kind: Annotated[
        str | None,
        Query(min_length=1, max_length=50, description="로그 분류 정확 일치"),
    ] = None,
    *,
    user_id: CurrentUserId,
    db: DBSession,
):
    """소유한 Run의 구조화된 진단 기록을 페이지로 조회합니다.

    프론트 진행/HITL/재접속은 SSE를 사용합니다.
    payload는 기록 종류별로 다르며, 로그 저장 시각 순서는
    SSE sequence나 실행의 인과 순서가 아닙니다.
    """
    return await list_diagnostic_logs(
        db,
        user_id=user_id,
        session_id=session_id,
        run_id=run_id,
        params=params,
        agent_name=agent_name,
        node=node,
        event=event,
        kind=kind,
    )


@router.post(
    "/sessions/{session_id}/runs/{run_id}/cancel",
    response_model=PublicRunResource,
    status_code=status.HTTP_202_ACCEPTED,
)
async def cancel_run(
    session_id: UUID,
    run_id: UUID,
    payload: RunCancel,
    user_id: CurrentUserId,
    db: DBSession,
):
    return await PublicRunService.cancel(
        db, user_id, session_id, run_id, payload
    )


@router.get("/sessions/{session_id}/runs/{run_id}/stream")
async def stream_run(
    request: Request,
    session_id: UUID,
    run_id: UUID,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    *,
    user_id: StreamUserId,
    db: StreamDBSession,
):
    """Replay durable events after Last-Event-ID, then wait for commit
    notifications.
    """

    public = await PublicRunService.read(db, user_id, session_id, run_id)
    initial_sequence = parse_sequence(last_event_id)

    from dtest.api_service.streaming import RunStreamHub, RunStreamResponse

    application = getattr(request, "app", None)
    owned = (
        application is None
    )  # Direct adapter tests/development, outside ASGI.
    hub = (
        RunStreamHub(settings, session_factory=lambda: get_session_factory()())
        if owned
        else application.state.run_stream_hub
    )

    if not owned:
        return RunStreamResponse(
            hub,
            request,
            (user_id, session_id, public.run_id),
            initial_sequence,
        )

    async def event_generator():
        try:
            async with hub.subscribe(
                user_id, session_id, public.run_id
            ) as entry:
                async for chunk in hub.stream(
                    request, entry, initial_sequence
                ):
                    yield chunk
        finally:
            if owned:
                await hub.close()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
