"""Adapt only the confirmed URL string boundary of the corporate SDK."""

from typing import Any, cast

from fastapi import Request


class SdkRequestView:
    """Expose a string URL to the SDK without modifying the ASGI request.

    All other reads delegate to the original request. This is not a Flask
    request/session implementation and does not override query parameters.
    """

    __slots__ = ("_request",)

    def __init__(self, request: Request):
        self._request = request

    @property
    def url(self) -> str:
        return str(self._request.url)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._request, name)


def sdk_request(request: Request) -> Request:
    # Existing private SDK factories annotate their input as Request. The
    # foreign SDK consumes attributes, not the ASGI Request class itself.
    # Keep that callable signature while adapting only its runtime input.
    return cast(Request, SdkRequestView(request))
