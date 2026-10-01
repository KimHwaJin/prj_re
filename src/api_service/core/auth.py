"""Cookie login identity; keep service roles, admission locks and SSE DB scope."""
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.core.database import get_db
from api_service.core.enums import UserRole
from api_service.repositories.user_repository import UserRepository
from service_auth.sso.dependencies import get_login_session
from service_auth.sso.sessions import LoginSession


@dataclass(frozen=True)
class Actor:
    user_id: UUID  # Internal FK; never read directly from the header.
    public_user_id: str
    role: UserRole


async def _resolve_actor(session: LoginSession, db: AsyncSession, *, for_share=False) -> Actor:
    try:
        internal_id = UUID(session.user_id)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(401, "A valid login session is required.") from None
    user = await UserRepository.get_active(db, internal_id, for_share=for_share)
    if user is None:
        raise HTTPException(401, "A registered, active user is required.")
    return Actor(user.user_id, user.public_user_id, user.role)


async def get_current_actor(
    session: LoginSession = Depends(get_login_session),
    db: AsyncSession = Depends(get_db),
) -> Actor:
    return await _resolve_actor(session, db)


async def require_admin(actor: Actor = Depends(get_current_actor)) -> Actor:
    if actor.role != UserRole.ADMIN:
        raise HTTPException(403, "Administrator role is required.")
    return actor


async def get_current_user_id(
    session: LoginSession = Depends(get_login_session),
    db: AsyncSession = Depends(get_db),
) -> UUID:
    # Business requests share a user-row lock until their transaction ends.
    # Deletion takes an exclusive lock, so new admission cannot race past it.
    # Resolve and lock in a single query rather than repeating user lookups.
    return (await _resolve_actor(session, db, for_share=True)).user_id


async def get_stream_user_id(
    session: LoginSession = Depends(get_login_session),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> UUID:
    """SSE authentication must release its nested DB dependency before streaming."""
    return (await _resolve_actor(session, db)).user_id
