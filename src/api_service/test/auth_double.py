"""Business regression identity double ONLY; excluded from wheels, no production header fallback.

Historical CRUD/graph regressions exercise ownership and row locks without corporate SSO.
Cookie/CSRF admission is covered independently by the real SSO boundary tests.
"""
import time

from fastapi import HTTPException, Request
from sqlalchemy import select

from api_service.core.enums import DeleteYN
from api_service.models.common.user_model import UserModel
from api_service.core.user_identity import normalize_user_id
from api_service.repositories.user_repository import UserRepository
from service_auth.sso.dependencies import get_login_session
from service_auth.sso.sessions import LoginSession


async def session_for_public_id(db, public_id):
    try:
        public_id = normalize_user_id(public_id or "")
    except ValueError:
        raise HTTPException(401, "Test identity is missing.") from None
    user = await UserRepository.get_by_public_id(db, public_id, active_only=True)
    if user is None:
        raise HTTPException(401, "Test user is missing.")
    return LoginSession(str(user.user_id), "a" * 43, int(time.time()) + 600)


def install_business_identity_double(app, session_factory):
    async def identity(request: Request):
        try:
            public_id = normalize_user_id(request.headers.get("X-User-Id") or "")
        except ValueError:
            raise HTTPException(401, "Test identity is missing.") from None
        # This fixture-only lookup stands in for Redis. Release it before the
        # real admission transaction; it must not hold a connection during SSE.
        async with session_factory() as db:
            internal_id = await db.scalar(select(UserModel.user_id).where(
                UserModel.public_user_id == public_id, UserModel.delete_yn == DeleteYN.N
            ).execution_options(test_identity_lookup=True))
        if internal_id is None:
            raise HTTPException(401, "Test user is missing.")
        return LoginSession(str(internal_id), "a" * 43, int(time.time()) + 600)
    app.dependency_overrides[get_login_session] = identity
