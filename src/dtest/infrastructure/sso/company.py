"""FastAPI 요청과 사내 SSO SDK 사이의 동기식 경계.

사내에서는 `_create_sdk`에 공식 SDK 생성·복귀 주소 설정을 연결한다.
SDK 소스·회사 쿠키·직원 정보는 로그나 외부 저장소에 기록하지 않는다.
"""

from collections.abc import Callable
from functools import partial
from typing import Protocol

from fastapi import Request

from dtest.contracts.auth import VerifiedEmployee
from dtest.infrastructure.sso.adapter import SyncSsoAdapter
from dtest.settings.auth import SsoSettings


class CompanySdk(Protocol):
    """제공받은 사내 SDK의 호출 계약. 별도 인증 프로토콜을 추측하지 않는다."""

    redirect_url: str

    def check_day_cookie(self, cookie: str) -> bool: ...

    def get_sso_info(self, cookie: str) -> object: ...


SdkFactory = Callable[[Request, str | None], CompanySdk]


def _create_sdk(request: Request, return_url: str | None = None) -> CompanySdk:
    # 폐쇄망에서 공식 SDK 사용법에 따라 생성한다.
    # request는 원본 FastAPI 요청, return_url은 서버가 정한 복귀 주소다.
    # 로그인 URL 생성 시 return_url을 SDK의 공식 방식으로 반영한다.
    # SDK 내부 요청 형태를 여기서 추측해 재구현하지 않는다.
    # SDK 자체의 연결/읽기 타임아웃도 공식 지원 방식으로 설정한다.
    raise NotImplementedError("Configure the corporate SSO SDK constructor.")


def _employee(info: object) -> VerifiedEmployee:
    # Flask 예시의 다섯 변수 대입 순서와 동일한 계약이다.
    if not isinstance(info, (tuple, list)) or len(info) != 5:
        raise ValueError("Invalid corporate SSO employee response.")
    employee_id, name, english_name, department, email = info
    if (
        not isinstance(employee_id, str)
        or not employee_id.strip()
        or not isinstance(name, str)
        or not name.strip()
        or any(
            value is not None and not isinstance(value, str)
            for value in (english_name, department, email)
        )
    ):
        raise ValueError("Invalid corporate SSO employee response.")
    return VerifiedEmployee(
        employee_id=employee_id,
        display_name=name,
        english_name=english_name,
        department=department,
        email=email,
    )


def verify_employee(
    request: Request, *, sdk_factory: SdkFactory | None = None
) -> VerifiedEmployee | None:
    cookie = request.headers.get("cookie")
    if not cookie:
        return None
    sdk = (sdk_factory or _create_sdk)(request, None)
    valid = sdk.check_day_cookie(cookie)
    if valid is False:
        return None
    if valid is not True:
        raise ValueError("Invalid corporate SSO verification response.")
    # 검증 성공 이후에만 직원 정보를 조회한다. 사번의 앞자리 0을 보존한다.
    return _employee(sdk.get_sso_info(cookie))


def build_login_url(
    request: Request,
    return_url: str,
    *,
    sdk_factory: SdkFactory | None = None,
) -> str:
    sdk = (sdk_factory or _create_sdk)(request, return_url)
    # SDK가 만든 URL을 이어 붙이거나 재인코딩하지 않는다.
    # 이동 대상 origin 검사는 공통 SsoRuntime이 담당한다.
    return sdk.redirect_url


def create_adapter(
    settings: SsoSettings, *, sdk_factory: SdkFactory | None = None
) -> SyncSsoAdapter:
    """공통 timeout 설정 아래에서 SDK를 thread pool으로 실행한다."""
    return SyncSsoAdapter(
        partial(verify_employee, sdk_factory=sdk_factory),
        partial(build_login_url, sdk_factory=sdk_factory),
    )
