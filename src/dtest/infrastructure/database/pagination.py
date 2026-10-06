"""Stable cursor pagination for collection queries."""

import base64
import json
from datetime import datetime
from uuid import UUID
from sqlalchemy import and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from dtest.contracts.errors import ApplicationError
from dtest.contracts.pagination import ListParams
from dtest.contracts.resources.api_schema import PageInfo


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        return datetime.fromisoformat(value["created_at"]), UUID(value["id"])
    except (
        KeyError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise ApplicationError(
            status_code=400, detail="Invalid cursor."
        ) from exc


def make_cursor(item, *, id_name: str) -> str:
    payload = json.dumps(
        {
            "created_at": item.created_at.isoformat(),
            "id": str(getattr(item, id_name)),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


async def fetch_page(
    db: AsyncSession,
    stmt,
    *,
    model,
    id_name: str,
    params: ListParams,
) -> tuple[list, PageInfo]:
    created_at = model.created_at
    resource_id = getattr(model, id_name)
    descending = params.sort.startswith("-")

    if params.created_at_from:
        stmt = stmt.where(created_at >= params.created_at_from)
    if params.created_at_to:
        stmt = stmt.where(created_at < params.created_at_to)
    if params.cursor:
        cursor_time, cursor_id = _decode_cursor(params.cursor)
        if descending:
            stmt = stmt.where(
                or_(
                    created_at < cursor_time,
                    and_(created_at == cursor_time, resource_id < cursor_id),
                )
            )
        else:
            stmt = stmt.where(
                or_(
                    created_at > cursor_time,
                    and_(created_at == cursor_time, resource_id > cursor_id),
                )
            )

    order = (
        (created_at.desc(), resource_id.desc())
        if descending
        else (created_at.asc(), resource_id.asc())
    )
    items = list(
        (await db.scalars(stmt.order_by(*order).limit(params.limit + 1))).all()
    )
    has_next = len(items) > params.limit
    items = items[: params.limit]
    return items, PageInfo(
        has_next=has_next,
        next_cursor=make_cursor(items[-1], id_name=id_name)
        if has_next and items
        else None,
    )
