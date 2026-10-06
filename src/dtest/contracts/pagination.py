from dataclasses import dataclass
from datetime import datetime

@dataclass(frozen=True)
class ListParams:
    limit: int = 50
    cursor: str | None = None
    sort: str = "-created_at"
    created_at_from: datetime | None = None
    created_at_to: datetime | None = None
