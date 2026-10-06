"""Borrowed Executor transport; the service runtime owns its open/close lifetime."""
from typing import Any, Protocol


class ExecutorTransport(Protocol):
    async def request(
        self, method: str, url: str, payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...
