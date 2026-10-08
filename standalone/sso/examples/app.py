"""Existing FastAPI app integration. JSON/env loading belongs to the host."""

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from sso import LoginDependency, SsoSettings, VerifiedEmployee, attach_sso


class ExampleUsers:
    async def bind(self, employee: VerifiedEmployee) -> str:
        # Demonstration only: uses the verified employee ID, no user DB.
        # Replace with a short transaction: lookup/register/disabled check.
        # First-user defaults and admin role policies belong to this service.
        return employee.employee_id


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # This can be merged into an existing lifespan. SSO never replaces it.
    path = Path(os.environ.get("SSO_CONFIG", "config.local.json"))
    config = json.loads(path.read_text(encoding="utf-8"))
    runtime = attach_sso(
        app,
        settings=SsoSettings(**config["sso"]),
        users=ExampleUsers(),
        redis_url=config["redis_url"],
    )
    try:
        yield
    finally:
        await runtime.close()


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/demo", response_class=HTMLResponse)
async def demo() -> str:
    return (
        '<a href="/api/v1/auth/login/sso?return_to=/demo">SSO login</a>'
        '<p>After login: <a href="/api/v1/auth/session">session</a></p>'
    )


@app.get("/items")
async def items(session: LoginDependency) -> dict[str, str]:
    # Access checks and current DB role/deactivation checks go here.
    return {"user_id": session.user_id}


@app.post("/items")
async def create_item(session: LoginDependency) -> dict[str, str]:
    # Cookie + X-CSRF-Token are required; authorization is a host policy.
    return {"created_by": session.user_id}
