from dataclasses import dataclass
from uuid import UUID
from dtest.contracts.enums import UserRole


@dataclass(frozen=True)
class Actor:
    user_id: UUID  # Internal FK; never read directly from the header.
    public_user_id: str
    role: UserRole
