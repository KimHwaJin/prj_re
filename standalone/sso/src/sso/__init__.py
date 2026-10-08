"""Independent SSO support for existing FastAPI applications."""

from sso.contracts import SsoAdapter, UserDirectory, VerifiedEmployee
from sso.dependencies import LoginDependency, get_login_session
from sso.errors import SsoError
from sso.runtime import SessionRead, SsoRuntime, attach_sso
from sso.sdk.adapter import SyncSsoAdapter
from sso.sdk.company import CompanySdk, SdkFactory, create_sdk_adapter
from sso.settings import SsoSettings
from sso.storage.sessions import LoginSession

__all__ = [
    "CompanySdk",
    "LoginDependency",
    "LoginSession",
    "SdkFactory",
    "SessionRead",
    "SsoAdapter",
    "SsoError",
    "SsoRuntime",
    "SsoSettings",
    "SyncSsoAdapter",
    "UserDirectory",
    "VerifiedEmployee",
    "attach_sso",
    "create_sdk_adapter",
    "get_login_session",
]
