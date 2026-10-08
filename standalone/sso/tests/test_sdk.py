"""Confirmed SDK protocol and raw nested URL handler reproduction."""

import threading
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from fastapi import FastAPI, Request

from conftest import MemoryRedis, Users
from sso import SsoSettings, VerifiedEmployee, attach_sso, create_sdk_adapter
from sso.sdk.adapter import load_adapter
from sso.sdk.company import verify_employee
from sso.sdk.request import SdkRequestView

PROFILE = ("000123", "홍길동", "Hong", "분석부", "hong@example.test")


class SdkDouble:
    def __init__(self, request: Request, return_url: str | None):
        self.thread = threading.get_ident()
        assert isinstance(request, SdkRequestView)
        self.request = request
        self.info: object = PROFILE
        self.info_calls = 0
        self.valid = True
        # Deliberately use the raw URL nesting confirmed in the private SDK.
        args = request.args.to_dict()
        cookies = request.cookies.to_dict()
        assert isinstance(request.url, str)
        assert request.url.startswith(("http://", "https://"))
        assert request.environ["HTTP_HOST"]
        target = args.get("ORIGIN", "http://localhost:5000")
        assert isinstance(target, str)
        self.target = target
        handler = "https://handler.example.test/auth?URL=" + self.target
        self.handler_target = parse_qs(urlsplit(handler).query)["URL"][0]
        self.redirect_url = "https://sso.example.test/login?" + urlencode(
            {"redirect_uri": self.handler_target}
        )
        self.cookies: dict[str, str | list[str]] = cookies

    def check_day_cookie(self, cookie: str) -> bool:
        return (
            self.valid if self.valid is not True else "company=valid" in cookie
        )

    def get_sso_info(self, cookie: str) -> object:
        self.info_calls += 1
        return self.info


class Factory:
    def __init__(self):
        self.instances: list[SdkDouble] = []

    def __call__(self, request: Request, return_url: str | None) -> SdkDouble:
        sdk = SdkDouble(request, return_url)
        self.instances.append(sdk)
        return sdk


@pytest.mark.parametrize("secure", [True, False])
@pytest.mark.parametrize("target", ["app", "docs"])
async def test_realistic_sdk_http_round_trip(secure: bool, target: str):
    factory, users, redis = Factory(), Users(), MemoryRedis()
    origin = "https://api.example.test" if secure else "http://localhost:5000"
    settings = SsoSettings(
        namespace="sdk:test:sso",
        cookie_name="sdk_session",
        public_api_origin=origin,
        frontend_origin=origin,
        allowed_origins=("https://sso.example.test",),
        allowed_return_roots=("/demo",),
        cookie_secure=secure,
    )
    app = FastAPI()
    runtime = attach_sso(
        app,
        settings=settings,
        users=users,
        redis=redis,
        redis_url="redis://unused",
        adapter=create_sdk_adapter(factory),
    )
    main_thread = threading.get_ident()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=origin,
    ) as client:
        response = await client.get(
            "/api/v1/auth/login/sso",
            params={
                "return_to": "/demo",
                "target": target,
                "ORIGIN": "https://evil.test",
            },
        )
        assert response.status_code == 302
        callback = parse_qs(urlsplit(response.headers["location"]).query)[
            "redirect_uri"
        ][0]
        assert callback.startswith(origin + "/api/v1/auth/login/sso/callback/")
        assert not urlsplit(callback).query and "evil.test" not in callback
        sdk = factory.instances[0]
        assert sdk.handler_target == callback
        assert sdk.thread != main_thread
        assert sdk.request.query_params["ORIGIN"] == "https://evil.test"
        assert sdk.request.args.to_dict()["ORIGIN"] == callback
        assert isinstance(sdk.request, SdkRequestView)
        client.cookies.set("company", "valid")
        response = await client.get(callback)
        assert response.status_code == 302
        assert response.headers["location"] == origin + (
            "/docs" if target == "docs" else "/demo"
        )
        assert users.employees == [
            VerifiedEmployee(
                "000123",
                "홍길동",
                english_name="Hong",
                department="분석부",
                email="hong@example.test",
            )
        ]
        session = await client.get("/api/v1/auth/session")
        assert session.status_code == 200
        assert session.json()["user_id"] == "internal-user-id"
        assert "hong@example.test" not in str(redis.data)
        assert (await client.get(callback)).status_code == 400
    await runtime.close()


@pytest.mark.parametrize(
    "info",
    [
        None,
        PROFILE[:-1],
        (*PROFILE, "extra"),
        (123, *PROFILE[1:]),
        (" ", *PROFILE[1:]),
        (*PROFILE[:2], {}, *PROFILE[3:]),
        (*PROFILE[:4], 123),
    ],
)
async def test_invalid_profile_never_registers(info: object):
    factory = Factory()

    def invalid(request: Request, return_url: str | None) -> SdkDouble:
        sdk = factory(request, return_url)
        sdk.info = info
        return sdk

    request = Request(
        {
            "type": "http",
            "scheme": "https",
            "method": "GET",
            "path": "/",
            "server": ("api.example.test", 443),
            "headers": [
                (b"host", b"api.example.test"),
                (b"cookie", b"company=valid"),
            ],
            "query_string": b"",
        }
    )
    with pytest.raises(ValueError, match="employee response"):
        await create_sdk_adapter(invalid).verify(request)


def test_no_cookie_does_not_construct_sdk():
    factory = Factory()
    request = Request({"type": "http", "headers": []})
    assert verify_employee(request, sdk_factory=factory) is None
    assert not factory.instances


async def test_unconfigured_factory_fails_closed():
    settings = SsoSettings(namespace="sdk:test", cookie_name="test_session")
    request = Request({"type": "http", "headers": []})
    with pytest.raises(Exception) as error:
        await load_adapter(settings).verify(request)
    assert getattr(error.value, "status_code", None) == 503


@pytest.mark.parametrize("factory", ["missing.module:factory", "sso:missing"])
def test_invalid_factory_error_does_not_expose_import_details(factory: str):
    settings = SsoSettings(
        namespace="sdk:test",
        cookie_name="test_session",
        adapter_factory=factory,
    )
    with pytest.raises(ValueError, match="private adapter contract"):
        load_adapter(settings)
