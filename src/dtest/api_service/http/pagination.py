from datetime import datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Query

from dtest.contracts.pagination import ListParams


def list_params(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query()] = None,
    sort: Annotated[str, Query(pattern="^-?created_at$")] = "-created_at",
    created_at_from: Annotated[datetime | None, Query()] = None,
    created_at_to: Annotated[datetime | None, Query()] = None,
) -> ListParams:
    if created_at_from and created_at_to and created_at_from >= created_at_to:
        raise HTTPException(
            status_code=422,
            detail="created_at_from must be earlier than created_at_to.",
        )
    return ListParams(limit, cursor, sort, created_at_from, created_at_to)


ListQuery = Annotated[ListParams, Depends(list_params)]
