"""Administrator list projection; no per-user project lookup or mutation."""
from dtest.contracts.errors import ApplicationError
from sqlalchemy import or_, select
from sqlalchemy.orm import Bundle

from dtest.contracts.actors import Actor
from dtest.contracts.enums import DeleteYN, UserRole
from dtest.contracts.pagination import ListParams
from dtest.infrastructure.database.pagination import fetch_page
from dtest.infrastructure.database.models.user_model import UserModel as User
from dtest.contracts.resources.user_schema import UserListStatus, UserSummary

# The internal UUID is used only for cursor tie-breaking. The response user_id
# is the public string ID; no ORM objects or user rows are cached here.
USER_SUMMARIES = select(Bundle("user_summary",
    User.user_id, User.public_user_id, User.user_name, User.role,
    (User.delete_yn == DeleteYN.N).label("is_active"),
    User.created_at, User.updated_at, User.deleted_at,
))


async def list_user_summaries(
    db, actor: Actor, params: ListParams, *, q: str | None = None,
    role: UserRole | None = None, status: UserListStatus = "active",
):
    if actor.role != UserRole.ADMIN:
        raise ApplicationError(403, "Administrator role is required.")
    stmt = USER_SUMMARIES
    if status != "all":
        stmt = stmt.where(User.delete_yn == (DeleteYN.N if status == "active" else DeleteYN.Y))
    if role is not None:
        stmt = stmt.where(User.role == role)
    if q is not None:
        term = q.strip()
        if not term:
            raise ApplicationError(422, "q must contain non-whitespace characters.")
        # Search input is a literal substring, not a SQL LIKE expression.
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = "%" + escaped + "%"
        stmt = stmt.where(or_(User.public_user_id.ilike(pattern, escape="\\"),
                              User.user_name.ilike(pattern, escape="\\")))
    rows, page = await fetch_page(db, stmt, model=User, id_name="user_id", params=params)
    return {"items": [UserSummary(
        user_id=row.public_user_id, user_name=row.user_name, role=row.role,
        is_active=row.is_active, created_at=row.created_at, updated_at=row.updated_at,
        deleted_at=row.deleted_at,
    ) for row in rows], "page": page}
