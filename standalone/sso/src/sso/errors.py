"""Storage failures work with FastAPI's built-in HTTP exception handler."""

from fastapi import HTTPException


class SsoError(HTTPException):
    """SSO-specific error; installing SSO does not replace app handlers."""
