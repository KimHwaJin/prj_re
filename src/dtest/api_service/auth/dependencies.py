from fastapi import Header, HTTPException, Request

from dtest.infrastructure.redis.login_sessions import LoginSession


async def get_login_session(
    request: Request,
    csrf_token: str | None = Header(
        default=None,
        alias="X-CSRF-Token",
        description=(
            "Required for cookie-authenticated POST/PUT/PATCH/DELETE requests."
        ),
    ),
) -> LoginSession:
    runtime = getattr(request.app.state, "sso", None)
    if runtime is None:
        raise HTTPException(503, "Authentication runtime is unavailable.")
    return await runtime.authenticate(request, csrf_token)
