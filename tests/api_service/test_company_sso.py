"""사내 SDK 요청 계약과 FastAPI 로그인 왕복. 실제 SDK는 폐쇄망에서 검증."""

from typing import Annotated
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from fastapi import Depends, FastAPI, Request
from starlette.datastructures import QueryParams

from dtest.api_service.auth.dependencies import get_login_session
from dtest.api_service.auth.runtime import attach_sso
from dtest.contracts.auth import VerifiedEmployee
from dtest.infrastructure.redis.login_sessions import LoginSession
from dtest.infrastructure.sso import company
from dtest.infrastructure.sso.adapter import load_adapter
from dtest.infrastructure.sso.company import (
    build_login_url,
    create_adapter,
    verify_employee,
)
from dtest.infrastructure.sso.request import SdkQueryArgs
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
    def __init__(self, request: Request, return_url: str | None):
        self.request = request
        self.return_url = return_url
        self.checked: list[str] = []
        self.info_calls: list[str] = []
        self.info: object = EMPLOYEE
        self.valid: bool = True
        self.error: Exception | None = None
        self.redirect_url = "https://sso.example.test/login?" + urlencode(
            {"callback": return_url or ""}
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

    def __call__(self, request: Request, return_url: str | None) -> SdkDouble:
        sdk = SdkDouble(request, return_url)
        sdk.info, sdk.error = self.info, self.error
        self.instances.append(sdk)
        return sdk


class FlaskRequestSdkDouble(SdkDouble):
    def __init__(self, sdk_request, return_url):
        # Simulate the SDK constructor reported by the user. A raw FastAPI
        # URL has no startswith method; the bridge must handle both paths.
        assert sdk_request.url.startswith(("http://", "https://"))
        self.origin = sdk_request.args.to_dict().get("ORIGIN")
        assert sdk_request.args.get("ORIGIN") == self.origin
        super().__init__(sdk_request, return_url)


class FlaskRequestSdkFactory(SdkFactoryDouble):
    def __call__(self, request: Request, return_url: str | None) -> SdkDouble:
        sdk = FlaskRequestSdkDouble(request, return_url)
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


def test_sdk_factory_receives_original_request_and_explicit_callback():
    original = request("ORIGIN=https%3A%2F%2Fevil.test&other=value")
    factory = SdkFactoryDouble()
    callback = "https://api.example.test/callback"
    build_login_url(original, callback, sdk_factory=factory)
    sdk = factory.instances[0]
    assert sdk.request is original
    assert sdk.return_url == callback
    assert original.query_params["ORIGIN"] == "https://evil.test"
    assert original.query_params["other"] == "value"


def test_employee_verification_passes_cookie_without_request_conversion():
    original = request("other=value", cookie="company=valid")
    factory = SdkFactoryDouble()
    verify_employee(original, sdk_factory=factory)
    sdk = factory.instances[0]
    assert sdk.request is original
    assert sdk.return_url is None
    assert sdk.checked == sdk.info_calls == ["company=valid"]


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
    assert parse_qs(urlsplit(url).query)["callback"] == [callback]


@pytest.mark.asyncio
async def test_login_round_trip_uses_string_url_sdk_and_keeps_profile_private(
    monkeypatch: pytest.MonkeyPatch,
):
    factory, users, redis = (
        FlaskRequestSdkFactory(),
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
            "callback"
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


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["verify", "login_url"])
async def test_sdk_string_url_keeps_original_request_and_callback(operation):
    original = Request(
        {
            "type": "http",
            "scheme": "https",
            "server": ("api.example.test", 443),
            "method": "GET",
            "path": "/api/v1/auth/login/sso",
            "headers": [(b"cookie", b"company=valid")],
            "query_string": b"ORIGIN=https%3A%2F%2Fevil.test&other=value",
        }
    )
    original_url = original.url
    with pytest.raises(AttributeError) as failure:
        FlaskRequestSdkDouble(original, None)
    assert failure.value.name == "startswith"
    assert failure.value.obj is original_url
    factory = FlaskRequestSdkFactory()
    adapter = create_adapter(SsoSettings(), sdk_factory=factory)
    callback = "https://api.example.test/callback"
    if operation == "verify":
        assert await adapter.verify(original) == VerifiedEmployee(
            "000123",
            "홍길동",
            english_name=EMPLOYEE[2],
            department=EMPLOYEE[3],
            email=EMPLOYEE[4],
        )
    else:
        await adapter.login_url(original, callback)
    sdk = factory.instances[0]
    assert isinstance(sdk, FlaskRequestSdkDouble)
    assert sdk.request is not original
    assert sdk.request.url == str(original_url)
    assert sdk.request.headers is original.headers
    assert sdk.request.query_params is original.query_params
    assert sdk.request.scope is original.scope
    assert sdk.request.method == "GET"
    assert sdk.request.query_params["ORIGIN"] == "https://evil.test"
    assert sdk.origin == "https://evil.test"
    assert sdk.return_url == (None if operation == "verify" else callback)
    assert original.url is original_url
    assert not isinstance(original.url, str)
    if operation == "verify":
        assert sdk.checked == sdk.info_calls == ["company=valid"]


@pytest.mark.parametrize(
    ("query", "flat", "expected"),
    [
        ("", True, {}),
        ("blank=&flag", True, {"blank": "", "flag": ""}),
        (
            "name=%ED%99%8D&next=%2Fdemo%3Fa%3D1",
            True,
            {"name": "홍", "next": "/demo?a=1"},
        ),
        ("tag=first&tag=second", True, {"tag": "first"}),
        ("tag=first&tag=second", False, {"tag": ["first", "second"]}),
    ],
)
def test_sdk_args_to_dict_preserves_flask_query_semantics(
    query,
    flat,
    expected,
):
    params = QueryParams(query)
    args = SdkQueryArgs(params)
    assert args.to_dict(flat=flat) == expected
    assert len(args) == len(params)
    assert args.get("absent") is None
    assert args.get("absent", "fallback") == "fallback"
    assert "absent" not in args
    with pytest.raises(KeyError):
        args["absent"]
    if "tag" in args:
        assert args["tag"] == args.get("tag") == "first"
        assert dict(args) == {"tag": "first"}
        result = args.to_dict(flat=False)
        values = result["tag"]
        assert isinstance(values, list)
        values.append("changed")
        assert args.to_dict(flat=False) == {"tag": ["first", "second"]}
        assert params.getlist("tag") == ["first", "second"]
    assert params == QueryParams(query)
