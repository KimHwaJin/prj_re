"""Adapt confirmed Flask request attributes for the corporate SDK."""

from collections.abc import Iterator, Mapping
from typing import Any, cast

from fastapi import Request
from starlette.datastructures import QueryParams

from dtest.infrastructure.sso.environ import request_environ


class SdkQueryArgs(Mapping[str, str]):
    """Read-only Flask-style query access with first-value semantics.

    Unlike Starlette's last-value lookup, Flask MultiDict selects the first
    duplicate value. Copies returned by to_dict cannot change the request.
    """

    __slots__ = ("_values",)

    def __init__(self, params: QueryParams):
        self._values: dict[str, list[str]] = {}
        for key, value in params.multi_items():
            self._values.setdefault(key, []).append(value)

    def __getitem__(self, key: str) -> str:
        return self._values[key][0]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def to_dict(self, flat: bool = True) -> dict[str, str | list[str]]:
        return {
            key: values[0] if flat else list(values)
            for key, values in self._values.items()
        }


class SdkRequestView:
    """Expose SDK request metadata without modifying the ASGI request.

    URL, query args and environ are adapted. Other reads delegate to the
    original request. Flask session globals and callback URLs are unchanged.
    """

    __slots__ = ("_args", "_environ", "_request")

    def __init__(self, request: Request):
        self._request = request
        self._args = SdkQueryArgs(request.query_params)
        self._environ = request_environ(request)

    @property
    def environ(self) -> dict[str, str]:
        return self._environ

    @property
    def args(self) -> SdkQueryArgs:
        return self._args

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
