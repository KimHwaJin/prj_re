"""사내 SDK 요청 계약과 FastAPI 로그인 왕복. 실제 SDK는 폐쇄망에서 검증."""

from typing import Annotated
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from fastapi import Depends, FastAPI, Request

from dtest.api_service.auth.dependencies import get_login_session
from dtest.api_service.auth.runtime import attach_sso
from dtest.contracts.auth import VerifiedEmployee
from dtest.infrastructure.redis.login_sessions import LoginSession
from dtest.infrastructure.sso import company
from dtest.infrastructure.sso.adapter import load_adapter
from dtest.infrastructure.sso.company import (
    SsoRequest,
    build_login_url,
    create_adapter,
    verify_employee,
)
from dtest.settings.auth import SsoSettings
from tests.api_service.test_sso_auth import MemoryRedis

EMPLOYEE = (
    "000123",
    "홍길동",
    "Hong Gildong",
    "데이터분석부",
    "hong@example.test",
)


class SdkDouble:
    def __init__(self, request: SsoRequest):
        self.request = request
        self.checked: list[str] = []
        self.info_calls: list[str] = []
        self.info: object = EMPLOYEE
        self.valid: bool = True
        self.error: Exception | None = None
        self.redirect_url = "https://sso.example.test/login?" + urlencode(
            {"ORIGIN": request.args.to_dict().get("ORIGIN", "")}
        )

    def check_day_cookie(self, cookie: str) -> bool:
        self.checked.append(cookie)
        if self.error is not None:
            raise self.error
        return self.valid and cookie == "company=valid"

    def get_sso_info(self, cookie: str) -> object:
        self.info_calls.append(cookie)
        return self.info


class SdkFactoryDouble:
    def __init__(self):
        self.instances: list[SdkDouble] = []
        self.info: object = EMPLOYEE
        self.error: Exception | None = None

    def __call__(self, request: SsoRequest) -> SdkDouble:
        sdk = SdkDouble(request)
        sdk.info, sdk.error = self.info, self.error
        self.instances.append(sdk)
        return sdk


class UserDirectoryDouble:
    def __init__(self):
        self.employees: list[VerifiedEmployee] = []

    async def bind(self, employee: VerifiedEmployee) -> str:
        self.employees.append(employee)
        return "internal-user-id"


def request(query: str = "", cookie: str | None = None) -> Request:
    headers = [] if cookie is None else [(b"cookie", cookie.encode())]
    return Request(
        {"type": "http", "headers": headers, "query_string": query.encode()}
    )


def test_sdk_request_uses_copy_and_server_owned_origin():
    original = request("ORIGIN=https%3A%2F%2Fevil.test&other=value")
    facade = SsoRequest(original, origin="https://api.example.test/callback")
    copied = facade.args.to_dict()
    copied["ORIGIN"] = "https://changed.test"
    assert facade.args["ORIGIN"] == "https://api.example.test/callback"
    assert facade.args["other"] == "value"
    assert original.query_params["ORIGIN"] == "https://evil.test"
    assert "ORIGIN" not in SsoRequest(original).args
    assert facade.headers is original.headers


@pytest.mark.parametrize("cookie", [None, "", "company=invalid"])
def test_unauthenticated_never_reads_employee_info(cookie: str | None):
    factory = SdkFactoryDouble()
    assert verify_employee(request(cookie=cookie), sdk_factory=factory) is None
    assert all(not sdk.info_calls for sdk in factory.instances)
    if not cookie:
        assert not factory.instances


@pytest.mark.parametrize("info", [EMPLOYEE, list(EMPLOYEE)])
def test_verified_employee_contains_all_known_profile_fields(info: object):
    factory = SdkFactoryDouble()
    factory.info = info
    employee = verify_employee(
        request(cookie="company=valid"), sdk_factory=factory
    )
    assert employee == VerifiedEmployee(
        "000123",
        "홍길동",
        english_name="Hong Gildong",
        department="데이터분석부",
        email="hong@example.test",
    )
    assert factory.instances[0].checked == ["company=valid"]
    assert factory.instances[0].info_calls == ["company=valid"]


@pytest.mark.parametrize(
    "info",
    [
        None,
        {"emp_no": "000123"},
        EMPLOYEE[:-1],
        (*EMPLOYEE, "extra"),
        (123, *EMPLOYEE[1:]),
        (" ", *EMPLOYEE[1:]),
        (EMPLOYEE[0], None, *EMPLOYEE[2:]),
        (*EMPLOYEE[:2], {"private": "secret"}, *EMPLOYEE[3:]),
        (*EMPLOYEE[:3], ["department"], EMPLOYEE[4]),
        (*EMPLOYEE[:4], 123),
    ],
)
def test_malformed_success_is_an_error_not_anonymous_login(info: object):
    factory = SdkFactoryDouble()
    factory.info = info
    with pytest.raises(ValueError, match="^Invalid corporate SSO employee"):
        verify_employee(request(cookie="company=valid"), sdk_factory=factory)


def test_optional_profile_and_existing_positional_expiry_contract():
    factory = SdkFactoryDouble()
    factory.info = ("000123", "홍길동", None, None, None)
    assert verify_employee(
        request(cookie="company=valid"), sdk_factory=factory
    ) == VerifiedEmployee("000123", "홍길동")
    employee = VerifiedEmployee("000123", "홍길동", 2000000000)
    assert employee.valid_until_epoch == 2000000000
    assert employee.english_name is None


def test_login_url_preserves_sdk_query_and_encoded_callback():
    factory = SdkFactoryDouble()
    callback = (
        "https://api.example.test/api/v1/auth/login/sso?"
        "return_to=%2Fprojects%3Ffilter%3Dnew&target=app"
    )
    url = build_login_url(
        request("ORIGIN=https%3A%2F%2Fevil.test"),
        callback,
        sdk_factory=factory,
    )
    assert url == factory.instances[0].redirect_url
    assert parse_qs(urlsplit(url).query)["ORIGIN"] == [callback]


@pytest.mark.asyncio
async def test_login_round_trip_uses_company_factory_and_keeps_profile_private(
    monkeypatch: pytest.MonkeyPatch,
):
    factory, users, redis = (
        SdkFactoryDouble(),
        UserDirectoryDouble(),
        MemoryRedis(),
    )
    monkeypatch.setattr(company, "_create_sdk", factory)
    settings = SsoSettings(
        adapter_factory="dtest.infrastructure.sso.company:create_adapter",
        public_api_origin="https://api.example.test",
        frontend_origin="https://ui.example.test",
        allowed_origins=("https://sso.example.test",),
        allowed_return_roots=("/", "/projects"),
    )
    app = FastAPI()
    attach_sso(
        app,
        settings=settings,
        users=users,
        adapter=load_adapter(settings),
        redis=redis,
        redis_url="redis://unused-in-test",
        api_prefix="/api/v1",
        docs_path="/docs",
    )

    @app.get("/whoami")
    async def whoami(
        session: Annotated[LoginSession, Depends(get_login_session)],
    ) -> dict[str, str]:
        return {"id": session.user_id, "csrf": session.csrf_token}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://api.example.test",
    ) as client:
        response = await client.get(
            "/api/v1/auth/login/sso",
            params={"return_to": "/projects", "ORIGIN": "https://evil.test"},
        )
        assert response.status_code == 302
        assert not response.headers.get("set-cookie")
        assert not users.employees
        callback = parse_qs(urlsplit(response.headers["location"]).query)[
            "ORIGIN"
        ][0]
        assert callback.startswith("https://api.example.test/")
        assert "evil.test" not in callback
        assert parse_qs(urlsplit(callback).query) == {
            "return_to": ["/projects"],
            "target": ["app"],
        }
        client.cookies.set("company", "valid")
        response = await client.get(callback)
        assert response.status_code == 302
        assert response.headers["location"] == (
            "https://ui.example.test/projects"
        )
        assert "HttpOnly" in response.headers["set-cookie"]
        assert users.employees[0].email == EMPLOYEE[4]
        assert users.employees[0].department == EMPLOYEE[3]
        assert (await client.get("/whoami")).json()["id"] == "internal-user-id"
        serialized = str(redis.data)
        assert EMPLOYEE[4] not in serialized and EMPLOYEE[3] not in serialized
        assert "company=valid" not in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["outage", "invalid-info", "missing-sdk"])
async def test_sdk_failure_is_sanitized_and_never_registers_or_sets_cookie(
    failure: str,
    monkeypatch: pytest.MonkeyPatch,
):
    factory, users = SdkFactoryDouble(), UserDirectoryDouble()
    if failure == "outage":
        factory.error = RuntimeError("private-cookie-secret")
    if failure == "invalid-info":
        factory.info = {"private": "private-cookie-secret"}
    if failure != "missing-sdk":
        monkeypatch.setattr(company, "_create_sdk", factory)
    app = FastAPI()
    attach_sso(
        app,
        settings=SsoSettings(
            public_api_origin="https://api.example.test",
            frontend_origin="https://ui.example.test",
        ),
        users=users,
        adapter=create_adapter(SsoSettings()),
        redis=MemoryRedis(),
        redis_url="redis://unused-in-test",
        api_prefix="/api/v1",
        docs_path="/docs",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://api.example.test",
    ) as client:
        client.cookies.set("company", "valid")
        response = await client.get("/api/v1/auth/login/sso")
        assert response.status_code == 503
        assert response.json() == {"detail": "Corporate SSO is unavailable."}
        assert not response.headers.get("set-cookie")
        assert not users.employees
