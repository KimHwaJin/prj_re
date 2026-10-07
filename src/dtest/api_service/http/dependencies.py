"Cookie login identity; keep service roles, admission locks and SSE DB scope."

from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from dtest.api_service.auth.dependencies import LoginDependency
from dtest.contracts.actors import Actor
from dtest.contracts.enums import UserRole
from dtest.infrastructure.database.repositories.user_repository import (
    UserRepository,
)
from dtest.infrastructure.database.runtime import get_db, short_session
from dtest.infrastructure.redis.login_sessions import LoginSession

DBSession = Annotated[AsyncSession, Depends(get_db)]
StreamDBSession = Annotated[AsyncSession, Depends(get_db, scope="function")]


async def _resolve_actor(
    session: LoginSession, db: AsyncSession, *, for_share=False
) -> Actor:
    try:
        internal_id = UUID(session.user_id)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(
            401, "A valid login session is required."
        ) from None
    user = await UserRepository.get_active(
        db, internal_id, for_share=for_share
    )
    if user is None:
        raise HTTPException(401, "A registered, active user is required.")
    return Actor(user.user_id, user.public_user_id, user.role)


async def get_current_actor(
    session: LoginDependency,
    db: DBSession,
) -> Actor:
    return await _resolve_actor(session, db)


CurrentActor = Annotated[Actor, Depends(get_current_actor)]


async def require_admin(actor: CurrentActor) -> Actor:
    if actor.role != UserRole.ADMIN:
        raise HTTPException(403, "Administrator role is required.")
    return actor


async def get_current_user_id(
    session: LoginDependency,
    db: DBSession,
) -> UUID:
    # Business requests share a user-row lock until their transaction ends.
    # Deletion takes an exclusive lock, so new admission cannot race past it.
    # Resolve and lock in a single query rather than repeating user lookups.
    return (await _resolve_actor(session, db, for_share=True)).user_id


async def get_stream_user_id(
    session: LoginDependency,
    db: StreamDBSession,
) -> UUID:
    """SSE authentication must release its nested DB dependency before
    streaming.
    """
    return (await _resolve_actor(session, db)).user_id


async def get_read_user_id(session: LoginDependency) -> UUID:
    """Verify active identity before read-only external work, then release DB.

    This snapshot authorizes a read; it does not protect a later mutation from
    account deletion. Writers must keep their admission transaction and lock.
    """
    async with short_session() as db:
        actor = await _resolve_actor(session, db)
    return actor.user_id


AdminActor = Annotated[Actor, Depends(require_admin)]
CurrentUserId = Annotated[UUID, Depends(get_current_user_id)]
ReadUserId = Annotated[UUID, Depends(get_read_user_id)]
StreamUserId = Annotated[UUID, Depends(get_stream_user_id, scope="function")]
