"""Closed-network integration template. Do not publish corporate SDK source or credentials.

Configure SSO_ADAPTER_FACTORY=dtest.infrastructure.sso.company:create_adapter AFTER implementing
these two functions inside the corporate environment. No guessed SDK getters or Flask shim.
"""
from fastapi import Request

from dtest.settings.auth import SsoSettings
from dtest.infrastructure.sso.adapter import SyncSsoAdapter
from dtest.contracts.auth import VerifiedEmployee


def verify_employee(request: Request) -> VerifiedEmployee | None:
    # 1. Construct the actual corporate SSO object using its supported request interface.
    #    The Flask sample alone does not prove that SSO accepts a Starlette Request.
    # 2. Validate the original cookie with the corporate SDK's official verification method.
    # 3. Obtain employee_id/display_name from the validated SDK result, not body/X-User-Id.
    # 4. Return None only for an unauthenticated/invalid credential; outages must raise.
    #    If available, supply the SDK authentication expiry as valid_until_epoch.
    raise NotImplementedError("Implement corporate SDK verification in the closed network.")


def build_login_url(request: Request, return_url: str) -> str:
    # Use the documented SDK login start and ORIGIN/return-address contract.
    # Apply the corporate protocol's state/nonce/replay checks here where required.
    # If a separate GET/POST callback is required, implement that documented contract;
    # this template intentionally does not invent OAuth/OIDC/SAML behavior.
    raise NotImplementedError("Implement corporate SDK login URL in the closed network.")


def create_adapter(settings: SsoSettings) -> SyncSsoAdapter:
    # Configure the SDK's actual connect/read timeout here if required by its API.
    # The common async deadline cannot forcibly stop a blocking SDK worker thread.
    return SyncSsoAdapter(verify_employee, build_login_url)
