from datetime import datetime
from fastapi import HTTPException, Query
from dtest.contracts.pagination import ListParams


def list_params(
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    sort: str = Query(default="-created_at", pattern=r"^-?created_at$"),
    created_at_from: datetime | None = Query(default=None),
    created_at_to: datetime | None = Query(default=None),
) -> ListParams:
    if created_at_from and created_at_to and created_at_from >= created_at_to:
        raise HTTPException(
            status_code=422,
            detail="created_at_from must be earlier than created_at_to.",
        )
    return ListParams(limit, cursor, sort, created_at_from, created_at_to)
