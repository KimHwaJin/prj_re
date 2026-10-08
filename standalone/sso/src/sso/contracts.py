from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class VerifiedEmployee:
    """사내 SDK 검증에 성공한 직원 정보. 요청 body로 생성하지 않는다."""

    employee_id: str  # emp_no: 사번. 문자열로 유지하여 앞자리 0을 보존.
    display_name: str  # emp_name: 직원 이름.
    # SDK가 만료를 제공할 때만 사용. 현재 다섯 값에는 만료 정보가 없다.
    valid_until_epoch: int | None = None
    english_name: str | None = None  # emp_name_en: 영문 이름.
    department: str | None = None  # dept: SDK가 제공한 부서 값.
    email: str | None = None  # email: 회사 메일 주소.


class SsoAdapter(Protocol):
    async def verify(self, request: Any) -> VerifiedEmployee | None:
        """검증된 직원 또는 미인증 None. SDK 장애는 예외로 전달한다."""
        ...

    async def login_url(self, request: Any, return_url: str) -> str:
        """서버가 정한 복귀 주소로 사내 로그인 URL을 생성한다."""
        ...


class UserDirectory(Protocol):
    async def bind(self, employee: VerifiedEmployee) -> str:
        """직원을 활성 사용자에 연결하고 서비스의 최초 등록 정책을 적용한다."""
        ...
