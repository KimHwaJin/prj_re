import math
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator


def origin(value: str) -> str:
    """Configured origin only; do not derive authentication redirects from Host headers."""
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
        or "\\" in value
        or any(ord(char) <= 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError("Invalid origin")
    _ = parsed.port
    return value


class SsoSettings(BaseModel):
    """Source injection is the calling service's responsibility; no os.environ reads."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    # 폐쇄망에서 설치한 module:factory. factory(settings) -> async SsoAdapter.
    adapter_factory: str = ""
    public_api_origin: str | None = None
    frontend_origin: str | None = None
    allowed_origins: tuple[str, ...] = ()
    # 서비스별·환경별 구분. loader가 profile을 반영한 기본값을 결정한다.
    namespace: str = "dtest-agent:dev:sso"
    cookie_name: str = "dtest_session"
    cookie_secure: bool = True
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    session_ttl_seconds: int = Field(default=1800, ge=60, le=86400)
    # Login return context only; not the authenticated session or Run lifetime.
    login_flow_ttl_seconds: int = Field(default=300, ge=60, le=1800)
    auto_register: bool = (
        True  # 최초 직원은 일반 사용자+기본 프로젝트. 관리자 자동 부여 없음.
    )
    allowed_return_roots: tuple[str, ...] = ("/",)
    # Streams의 BLOCK 연결풀과 분리. 같은 Redis 서버·DB를 사용한다.
    redis_max_connections: int = Field(default=8, ge=1, le=128)
    redis_timeout_seconds: float = Field(default=3.0, gt=0)
    call_timeout_seconds: float = Field(default=10.0, gt=0)

    @model_validator(mode="after")
    def valid_settings(self):
        for value in (
            self.public_api_origin,
            self.frontend_origin,
            *self.allowed_origins,
        ):
            if value is not None:
                origin(value)
                if self.cookie_secure and not value.startswith("https://"):
                    raise ValueError("Secure-cookie origins must use HTTPS")
        if self.adapter_factory and not re.fullmatch(
            r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*",
            self.adapter_factory,
        ):
            raise ValueError("adapter_factory must be module:factory")
        if not re.fullmatch(r"[A-Za-z0-9:_-]{1,100}", self.namespace):
            raise ValueError("Invalid namespace")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", self.cookie_name):
            raise ValueError("Invalid cookie name")
        if self.cookie_name.startswith("__Host-") and not self.cookie_secure:
            raise ValueError("__Host- cookies require Secure")
        if self.cookie_samesite == "none" and not self.cookie_secure:
            raise ValueError("SameSite=None requires Secure")
        if any(
            not math.isfinite(v)
            for v in (self.redis_timeout_seconds, self.call_timeout_seconds)
        ):
            raise ValueError("Timeouts must be finite")
        for root in self.allowed_return_roots:
            if (
                not root.startswith("/")
                or root.startswith("//")
                or "\\" in root
                or any(part in {".", ".."} for part in root.split("/"))
                or any(char in root for char in "%?#")
                or any(ord(c) <= 32 for c in root)
            ):
                raise ValueError("Invalid return root")
        return self
