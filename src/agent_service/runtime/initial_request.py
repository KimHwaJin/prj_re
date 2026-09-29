"""Entry-node receipt; an input checkpoint alone does not prove processing."""
from functools import wraps
import inspect

from service_contracts.execution import InvocationNeedsRecovery
from service_contracts.initial_request import initial_identity


def _identity(state):
    identity = state.get("initial_request_identity")
    if identity is not None and identity != initial_identity(state):
        raise InvocationNeedsRecovery("Initial request identity does not match its input")
    return identity


def _result(result, identity):
    if identity is None:  # Direct development graph invocations remain supported.
        return result
    if not isinstance(result, dict):
        raise InvocationNeedsRecovery("Initial request node must return a state update")
    return {**result, "initial_request_receipt": identity}


def record_initial_request(node):
    """Checkpoint successful input handling and its receipt in one node update."""
    if inspect.iscoroutinefunction(node):
        @wraps(node)
        async def asynchronous(state, *args, **kwargs):
            identity = _identity(state)
            return _result(await node(state, *args, **kwargs), identity)
        return asynchronous

    @wraps(node)
    def synchronous(state, *args, **kwargs):
        identity = _identity(state)
        return _result(node(state, *args, **kwargs), identity)
    return synchronous
