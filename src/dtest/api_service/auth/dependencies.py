from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from dtest.infrastructure.redis.login_sessions import LoginSession


async def get_login_session(
    request: Request,
    csrf_token: Annotated[
        str | None,
        Header(
            alias="X-CSRF-Token",
            description=(
                "Required for cookie-authenticated "
                "POST/PUT/PATCH/DELETE requests."
            ),
        ),
    ] = None,
) -> LoginSession:
    runtime = getattr(request.app.state, "sso", None)
    if runtime is None:
        raise HTTPException(503, "Authentication runtime is unavailable.")
    return await runtime.authenticate(request, csrf_token)


LoginDependency = Annotated[LoginSession, Depends(get_login_session)]
