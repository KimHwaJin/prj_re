from dataclasses import dataclass
from typing import Protocol

from typing import Any


@dataclass(frozen=True)
class VerifiedEmployee:
    """Only construct after the corporate SDK validates the request, never from user input."""
    employee_id: str
    display_name: str
    valid_until_epoch: int | None = None


class SsoAdapter(Protocol):
    async def verify(self, request: Any) -> VerifiedEmployee | None:
        """Verified employee or None when unauthenticated. SDK outages must raise."""
        ...

    async def login_url(self, request: Any, return_url: str) -> str:
        """Build the corporate login URL with an explicit, server-owned return URL."""
        ...


class UserDirectory(Protocol):
    async def bind(self, employee: VerifiedEmployee) -> str:
        """Map a verified employee to an active internal user ID, applying service provisioning policy."""
        ...
