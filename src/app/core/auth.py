"""Authentication boundary for the API.

The application has no identity provider configured yet.  During local
development a Bearer token is the UUID of an active user.  Replacing this
function with JWT/OIDC validation does not affect route or service code.
"""

from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.repositories.user_repository import UserRepository


bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> UUID:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer authentication is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_id = UUID(credentials.credentials)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid bearer token.") from exc

    if await UserRepository.get_active(db, user_id) is None:
        # Do not reveal whether a deleted/non-existent user ID was supplied.
        raise HTTPException(status_code=401, detail="Invalid bearer token.")
    return user_id

