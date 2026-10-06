"""Transitional bridge for synchronous work inside migrated async nodes.

This does not turn HTTP/DB/files into native asynchronous I/O. It retains the
awaiter until the executor operation finishes, including repeated cancellation.
Full resource budgets and ArtifactStore ownership are separate work.
"""
import asyncio
from contextvars import copy_context
from functools import partial
from typing import Callable, ParamSpec, TypeVar

P = ParamSpec('P')
T = TypeVar('T')


async def run_sync(function: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    # Honor a pending cancellation before submitting a new side effect.
    await asyncio.sleep(0)
    operation = asyncio.get_running_loop().run_in_executor(
        None, copy_context().run, partial(function, *args, **kwargs)
    )
    cancelled = False
    while not operation.done():
        try:
            await asyncio.shield(operation)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    try:
        result = operation.result()
    except BaseException as exc:
        if cancelled:
            raise asyncio.CancelledError from exc
        raise
    if cancelled:
        raise asyncio.CancelledError
    return result


async def call_io(function, *args, **kwargs):
    """Invoke an async port or retain lifetime of a legacy sync adapter."""
    import inspect
    target = function if inspect.isfunction(function) else getattr(function, "__call__", function)
    if inspect.iscoroutinefunction(function) or inspect.iscoroutinefunction(target):
        return await function(*args, **kwargs)
    result = await run_sync(function, *args, **kwargs)
    return await result if inspect.isawaitable(result) else result
