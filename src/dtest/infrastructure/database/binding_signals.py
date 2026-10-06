"""Process-local hints after durable binding commits; never a work queue.

Embedded API and Event Worker use different pool objects. Match their target
connection info and namespace so a late binding can wake a scan that previously
saw no binding. Other Pods/restarts are covered by the unchanged periodic scan.
No extra PostgreSQL/Redis connection or published business event is created.
"""

from collections import defaultdict
from contextlib import contextmanager
from weakref import WeakSet
import asyncio

_subscribers: dict[tuple[str, str], WeakSet] = defaultdict(WeakSet)


@contextmanager
def binding_subscription(conninfo: str, namespace: str, wake: asyncio.Event):
    key = (conninfo, namespace)
    _subscribers[key].add(wake)
    try:
        yield
    finally:
        group = _subscribers.get(key)
        if group is not None:
            group.discard(wake)
            if not group:
                _subscribers.pop(key, None)


def binding_committed(conninfo: str, namespace: str) -> None:
    for wake in tuple(_subscribers.get((conninfo, namespace), ())):
        wake.set()
