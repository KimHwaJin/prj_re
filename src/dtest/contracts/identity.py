"""Public identity syntax shared by HTTP input and bootstrap commands."""
import re

_ID = re.compile(r"[a-z0-9][a-z0-9_.@-]{0,99}", re.ASCII)


def normalize_user_id(value: str) -> str:
    normalized = value.strip().lower()
    if not _ID.fullmatch(normalized) or normalized == "me":
        raise ValueError("user_id must be 1–100 ASCII letters/digits or ._@-; 'me' is reserved")
    return normalized
