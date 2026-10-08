"""Adapt confirmed Flask request attributes for the corporate SDK."""

from collections.abc import Iterable, Iterator, Mapping
from typing import Any, cast

from fastapi import Request
from starlette.datastructures import QueryParams

from sso.sdk.environ import request_environ


class SdkRequestValues(Mapping[str, str]):
    """Read-only SDK values with first-value lookup and copied to_dict output.

    Query arguments and parsed cookies share this small mapping contract.
    This does not implement the entire Flask MultiDict interface.
    """

    __slots__ = ("_values",)

    def __init__(self, items: Iterable[tuple[str, str]]):
        self._values: dict[str, list[str]] = {}
        for key, value in items:
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


class SdkQueryArgs(SdkRequestValues):
    """Keep every query value; single lookup selects the first duplicate."""

    __slots__ = ()

    def __init__(self, params: QueryParams):
        super().__init__(params.multi_items())


class SdkCookies(SdkRequestValues):
    """Keep Starlette's parsed cookie values without parsing them again."""

    __slots__ = ()

    def __init__(self, cookies: Mapping[str, str]):
        super().__init__(cookies.items())


class SdkRequestView:
    """Expose SDK request metadata without modifying the ASGI request.

    URL, args, cookies and environ are adapted. Login URL creation replaces
    only the SDK args ORIGIN with the server callback. The ASGI request and
    session globals remain unchanged; other reads delegate to the request.
    """

    __slots__ = ("_args", "_cookies", "_environ", "_request")

    def __init__(self, request: Request, return_url: str | None = None):
        self._request = request
        params = request.query_params
        if return_url is not None:
            # The confirmed SDK reads ORIGIN from args.to_dict(). Replace
            # every client-supplied duplicate with the server callback.
            params = QueryParams(
                [
                    (key, value)
                    for key, value in params.multi_items()
                    if key != "ORIGIN"
                ]
                + [("ORIGIN", return_url)]
            )
        self._args = SdkQueryArgs(params)
        self._cookies = SdkCookies(request.cookies)
        self._environ = request_environ(request)

    @property
    def cookies(self) -> SdkCookies:
        return self._cookies

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


def sdk_request(request: Request, return_url: str | None = None) -> Request:
    # Existing private SDK factories annotate their input as Request. The
    # foreign SDK consumes attributes, not the ASGI Request class itself.
    # Keep that callable signature while adapting only its runtime input.
    return cast(Request, SdkRequestView(request, return_url))
