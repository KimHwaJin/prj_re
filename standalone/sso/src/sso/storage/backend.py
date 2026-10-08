"""Only async Redis commands needed by the package; no global connection."""

from typing import Any, Protocol


class RedisBackend(Protocol):
    async def execute_command(self, *args: Any) -> Any: ...
