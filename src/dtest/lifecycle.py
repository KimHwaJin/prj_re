"""Cancellation-safe cleanup independent of API health and database state."""

import asyncio


async def protected_cleanup(awaitable):
    """Repeated owner cancellation must not strand its owned tasks/resources."""
    task = asyncio.create_task(awaitable, name="run-owned-cleanup")
    interrupted = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            interrupted = True
    result = task.result()
    if interrupted:
        raise asyncio.CancelledError
    return result
