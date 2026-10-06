"""A single project document on the official LangGraph Store."""

from hashlib import sha256
from typing import Protocol, Any


class MemoryStore(Protocol):
    async def aget(self, namespace: tuple, key: str) -> Any: ...


from dtest.contracts.project_memory import MAX_STORAGE_CHARS, MemoryLimit

MEMORY_KEY = "document"
MEMORY_SCHEMA_VERSION = 2


def memory_namespace(user_id, project_id):
    return ("dtest", "project_memory", str(user_id), str(project_id))


def receipt_namespace(user_id, project_id):
    return ("dtest", "project_memory_receipts", str(user_id), str(project_id))


def receipt_key(source_id):
    return sha256(source_id.encode()).hexdigest()


def memory_document(item, user_id, project_id):
    """Scope labels are not authorization; callers must check ownership."""
    if item is None:
        value = {
            "schema_version": MEMORY_SCHEMA_VERSION,
            "content": "",
            "version": 0,
            "updated_at": None,
        }
    else:
        if (
            item.namespace != memory_namespace(user_id, project_id)
            or item.key != MEMORY_KEY
        ):
            raise ValueError(
                "Store returned memory outside the requested identity"
            )
        value = dict(item.value)
        if value.get(
            "schema_version"
        ) != MEMORY_SCHEMA_VERSION or not isinstance(
            value.get("content"), str
        ):
            raise ValueError(
                "Invalid project memory document format; apply "
                "migrations "
                "first"
            )
        if type(value.get("version")) is not int or value["version"] < 1:
            raise ValueError("Invalid project memory document version")
    if len(value["content"]) > MAX_STORAGE_CHARS:
        raise MemoryLimit("Project memory exceeds the absolute document bound")
    return {**value, "user_id": str(user_id), "project_id": str(project_id)}


async def read_memory(store: MemoryStore, user_id, project_id):
    # One exact key read, no topic/receipt scan, model or embedding call.
    return memory_document(
        await store.aget(memory_namespace(user_id, project_id), MEMORY_KEY),
        user_id,
        project_id,
    )
