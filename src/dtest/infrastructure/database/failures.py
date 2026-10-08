"""Safe DB failure hints without SQL, parameters or connection credentials."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DatabaseFailure:
    cause_type: str
    sqlstate: str | None
    recovery: str


def database_failure(error: BaseException) -> DatabaseFailure | None:
    """Unwrap DBAPI causes; never expose the driver exception message."""
    seen: set[int] = set()
    current: BaseException | None = error
    for _ in range(8):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        state = getattr(current, "sqlstate", None) or getattr(
            current, "pgcode", None
        )
        if isinstance(state, str) and re.fullmatch(r"[0-9A-Z]{5}", state):
            recovery = {
                "42P01": "run_migrations_against_selected_database",
                "42703": "run_migrations_against_selected_database",
                "42501": "check_database_permissions",
                "42601": "inspect_query_syntax",
            }.get(state, "inspect_database_error")
            return DatabaseFailure(type(current).__name__, state, recovery)
        if (
            isinstance(current, TypeError)
            and str(current) == "connect() takes no keyword arguments"
        ):
            return DatabaseFailure(
                "TypeError", None, "reinstall_locked_psycopg_packages"
            )
        original = getattr(current, "orig", None)
        current = (
            original
            if isinstance(original, BaseException)
            else current.__cause__ or current.__context__
        )
    return None
