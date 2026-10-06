"""Owner-scoped session reads and page projections."""

from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from dtest.contracts.errors import ApplicationError
from dtest.contracts.enums import DeleteYN
from dtest.infrastructure.database.models import SessionModel
from dtest.infrastructure.database.repositories.session_repository import (
    SessionRepository,
)
from dtest.contracts.pagination import ListParams
from dtest.infrastructure.database.pagination import fetch_page
from dtest.application.resources.sessions import SessionService
from dtest.application.resources.session_activity import (
    SESSION_QUERY,
    resource,
)


async def require_owned_session(
    db: AsyncSession, user_id: UUID, session_id: UUID
) -> SessionModel:
    session = await SessionRepository.get_active_by_user(
        db, user_id=user_id, session_id=session_id
    )
    if session is None:
        # The same response is deliberately used for missing and inaccessible IDs.
        raise ApplicationError(status_code=404, detail="Session not found.")
    return session


async def read_session_resource(
    db, user_id, session_id, *, after_mutation=False
):
    query = SESSION_QUERY.where(
        SessionModel.session_id == session_id, SessionModel.user_id == user_id
    )
    # A concurrent soft delete after our successful commit must not turn the
    # completed create/rename into a misleading 404. Ordinary GET stays hidden.
    if not after_mutation:
        query = query.where(SessionModel.delete_yn == DeleteYN.N)
    row = await db.scalar(query)
    if row is None:
        raise ApplicationError(404, "Session not found.")
    return resource(row)


async def list_sessions(db, user_id, project_id, params):
    await SessionService._require_project(db, user_id, project_id)
    items, page = await fetch_page(
        db,
        SESSION_QUERY.where(
            SessionModel.user_id == user_id,
            SessionModel.project_id == project_id,
            SessionModel.delete_yn == DeleteYN.N,
        ),
        model=SessionModel,
        id_name="session_id",
        params=params,
    )
    return {"items": [resource(item) for item in items], "page": page}
