"""Fill this file inside the closed network; do not modify the private SDK."""

from fastapi import Request

from sso import CompanySdk, SsoSettings, SyncSsoAdapter, create_sdk_adapter


def create_sdk(request: Request, return_url: str | None) -> CompanySdk:
    # request already supports SDK args/cookies.to_dict(), string url,
    # environ[HTTP_HOST]. On login, args[ORIGIN] is the trusted callback.
    # Replace the exception with the official SDK constructor, for example:
    # from your_private_module import SSO
    # return SSO(request)
    # Set the SDK's own HTTP connect/read timeouts using supported options.
    raise NotImplementedError("Connect the official SDK in the closed network")


def create_adapter(settings: SsoSettings) -> SyncSsoAdapter:
    return create_sdk_adapter(create_sdk)
