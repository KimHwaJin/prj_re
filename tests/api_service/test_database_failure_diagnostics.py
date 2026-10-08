"""DB startup hints distinguish schema errors without exposing SQL payloads."""

import asyncio
import logging

import asyncpg
import pytest
from sqlalchemy.exc import ProgrammingError

from dtest.bootstrap import BackgroundRuntime
from dtest.infrastructure.database.failures import database_failure


@pytest.mark.parametrize(
    "error_type,state,recovery",
    [
        (
            asyncpg.UndefinedTableError,
            "42P01",
            "run_migrations_against_selected_database",
        ),
        (
            asyncpg.UndefinedColumnError,
            "42703",
            "run_migrations_against_selected_database",
        ),
        (
            asyncpg.InsufficientPrivilegeError,
            "42501",
            "check_database_permissions",
        ),
        (asyncpg.PostgresSyntaxError, "42601", "inspect_query_syntax"),
    ],
)
def test_dbapi_error_preserves_safe_driver_cause(error_type, state, recovery):
    error = ProgrammingError("private SQL", {"secret": "value"}, error_type())
    failure = database_failure(error)
    assert failure is not None
    assert failure.cause_type == error_type.__name__
    assert failure.sqlstate == state
    assert failure.recovery == recovery
    assert "private" not in repr(failure)


def test_keyword_error_has_installation_hint_without_generic_typeerror_hint():
    failure = database_failure(
        TypeError("connect() takes no keyword arguments")
    )
    assert failure is not None
    assert failure.recovery == "reinstall_locked_psycopg_packages"
    assert database_failure(TypeError("private unrelated error")) is None


def test_chained_exception_cycles_are_bounded():
    error = RuntimeError("private")
    error.__cause__ = error
    assert database_failure(error) is None


def test_untrusted_sqlstate_is_not_logged():
    class InvalidStateError(RuntimeError):
        sqlstate = "postgresql://user:password@private/db"

    assert database_failure(InvalidStateError("private")) is None


@pytest.mark.asyncio
async def test_background_error_logs_cause_without_exception_payload(caplog):
    async def fail():
        raise ProgrammingError(
            "private SQL",
            {"password": "private-password"},
            asyncpg.UndefinedTableError("private-driver-message"),
        )

    runtime = BackgroundRuntime({}, timeout=1)
    task = asyncio.create_task(fail(), name="agent-run-worker")
    with pytest.raises(ProgrammingError):
        await task
    with caplog.at_level(logging.ERROR, logger="dtest.bootstrap"):
        runtime._observe(task)
    assert "error_type=ProgrammingError" in caplog.text
    assert "cause_type=UndefinedTableError" in caplog.text
    assert "sqlstate=42P01" in caplog.text
    assert "recovery=run_migrations_against_selected_database" in caplog.text
    assert "private" not in caplog.text
