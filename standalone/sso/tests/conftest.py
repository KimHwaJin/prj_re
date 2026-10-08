"""Independent fixtures: no imports from the host project."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest_asyncio
from fastapi import FastAPI, Request
from redis.exceptions import ConnectionError as RedisConnectionError

from sso import (
    LoginDependency,
    SsoRuntime,
    SsoSettings,
    VerifiedEmployee,
    attach_sso,
)


class MemoryRedis:
    def __init__(self):
        self.data: dict[str, tuple[str, int]] = {}
        self.now = 0
        self.fail = False

    async def execute_command(self, *args: Any) -> Any:
        if self.fail:
            raise RedisConnectionError("private-redis-secret")
        command = args[0]
        if command == "SET":
            _, key, value, _, ttl, _ = args
            if key in self.data and self.data[key][1] > self.now:
                return None
            self.data[key] = (value, self.now + ttl)
            return True
        key = args[-1]
        record = (
            self.data.pop(key, None)
            if command in {"EVAL", "DEL"}
            else self.data.get(key)
        )
        if command == "DEL":
            return bool(record)
        return record[0].encode() if record and record[1] > self.now else None


class CorporateDouble:
    def __init__(self):
        self.employee: VerifiedEmployee | None = VerifiedEmployee(
            "000123", "홍길동", email="hong@example.test"
        )
        self.url = "https://sso.example.test/login"
        self.callback: str | None = None
        self.error: Exception | None = None

    async def verify(self, request: Request) -> VerifiedEmployee | None:
        if self.error:
            raise self.error
        return self.employee

    async def login_url(self, request: Request, return_url: str) -> str:
        self.callback = return_url
        return self.url


class Users:
    def __init__(self):
        self.employees: list[VerifiedEmployee] = []
        self.result = "internal-user-id"

    async def bind(self, employee: VerifiedEmployee) -> str:
        self.employees.append(employee)
        return self.result


@dataclass
class Harness:
    app: FastAPI
    client: httpx.AsyncClient
    redis: MemoryRedis
    adapter: CorporateDouble
    users: Users
    runtime: SsoRuntime


@pytest_asyncio.fixture
async def h() -> AsyncIterator[Harness]:
    redis, adapter, users = MemoryRedis(), CorporateDouble(), Users()
    app = FastAPI()
    runtime = attach_sso(
        app,
        settings=SsoSettings(
            namespace="example:test:sso",
            cookie_name="example_session",
            public_api_origin="https://api.example.test",
            frontend_origin="https://ui.example.test",
            allowed_origins=("https://sso.example.test",),
            allowed_return_roots=("/", "/projects", "/demo"),
        ),
        users=users,
        adapter=adapter,
        redis=redis,
        redis_url="redis://unused-in-test",
    )

    @app.post("/business")
    async def business(session: LoginDependency) -> dict[str, str]:
        return {"user_id": session.user_id}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://api.example.test",
    ) as client:
        yield Harness(app, client, redis, adapter, users, runtime)
    await runtime.close()
