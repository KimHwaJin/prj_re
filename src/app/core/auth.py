"""Passwordless, trusted X-User-Id identity boundary; no token issuance."""
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.enums import UserRole
from app.core.user_identity import normalize_user_id
from app.repositories.user_repository import UserRepository

user_id_header = APIKeyHeader(name="X-User-Id", scheme_name="UserIdentity", auto_error=False)


@dataclass(frozen=True)
class Actor:
    user_id: UUID  # Internal FK; never read directly from the header.
    public_user_id: str
    role: UserRole


async def _resolve_actor(identity: str | None, db: AsyncSession, *, for_share=False) -> Actor:
    try:
        public_id = normalize_user_id(identity or "")
    except ValueError:
        raise HTTPException(401, "A registered, active X-User-Id is required.") from None
    user = await UserRepository.get_by_public_id(db, public_id, active_only=True, for_share=for_share)
    if user is None:
        raise HTTPException(401, "A registered, active X-User-Id is required.")
    return Actor(user.user_id, user.public_user_id, user.role)


async def get_current_actor(
    identity: str | None = Security(user_id_header),
    db: AsyncSession = Depends(get_db),
) -> Actor:
    return await _resolve_actor(identity, db)


async def require_admin(actor: Actor = Depends(get_current_actor)) -> Actor:
    if actor.role != UserRole.ADMIN:
        raise HTTPException(403, "Administrator role is required.")
    return actor


async def get_current_user_id(
    identity: str | None = Security(user_id_header),
    db: AsyncSession = Depends(get_db),
) -> UUID:
    # Business requests share a user-row lock until their transaction ends.
    # Deletion takes an exclusive lock, so new admission cannot race past it.
    # Resolve and lock in a single query rather than repeating user lookups.
    return (await _resolve_actor(identity, db, for_share=True)).user_id
