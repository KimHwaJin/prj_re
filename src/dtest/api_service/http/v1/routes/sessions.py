from dtest.application.resources.session_queries import (
    require_owned_session,
    read_session_resource,
    list_sessions,
)
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from dtest.api_service.http.dependencies import get_current_user_id
from dtest.infrastructure.database.runtime import get_db
from dtest.contracts.pagination import ListParams
from dtest.api_service.http.pagination import list_params
from dtest.contracts.resources.api_schema import Page, SessionResource
from dtest.contracts.resources.session_schema import (
    SessionCreate,
    SessionUpdate,
)
from dtest.application.resources.sessions import SessionService


router = APIRouter(tags=["sessions"])


@router.post(
    "/projects/{project_id}/sessions",
    response_model=SessionResource,
    status_code=status.HTTP_201_CREATED,
)
async def create_session(
    project_id: UUID,
    payload: SessionCreate,
    response: Response,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    session = await SessionService.create(db, user_id, project_id, payload)
    response.headers["Location"] = f"/api/v1/sessions/{session.session_id}"
    return await read_session_resource(
        db, user_id, session.session_id, after_mutation=True
    )


@router.get(
    "/projects/{project_id}/sessions", response_model=Page[SessionResource]
)
async def list_project_sessions(
    project_id: UUID,
    params: ListParams = Depends(list_params),
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await list_sessions(db, user_id, project_id, params)


@router.get("/sessions/{session_id}", response_model=SessionResource)
async def read_session(
    session_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await read_session_resource(db, user_id, session_id)


@router.patch("/sessions/{session_id}", response_model=SessionResource)
async def update_session(
    session_id: UUID,
    payload: SessionUpdate,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    session = await require_owned_session(db, user_id, session_id)
    updated = await SessionService.update(
        db, user_id, session.project_id, session_id, payload
    )
    return await read_session_resource(
        db, user_id, updated.session_id, after_mutation=True
    )


@router.delete(
    "/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_session(
    session_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    session = await require_owned_session(db, user_id, session_id)
    await SessionService.delete(db, user_id, session.project_id, session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
