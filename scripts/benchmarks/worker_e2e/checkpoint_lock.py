"""Supplemental exact lock telemetry, preserves the original saver lock."""

import time
from checkpoint_profile import _call


def install():
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    original = AsyncPostgresSaver.__init__

    class ObservedLock:
        def __init__(self, lock):
            self.lock = lock
            self.owner = None

        async def __aenter__(self):
            start = time.perf_counter()
            value = await self.lock.__aenter__()
            acquired = time.perf_counter()
            row = _call.get()
            self.owner = (row, start, acquired)
            return value

        async def __aexit__(self, *args):
            released = time.perf_counter()
            row, start, acquired = self.owner
            self.owner = None
            if row is not None:
                row.setdefault("locks", []).append(
                    dict(
                        wait_ms=(acquired - start) * 1000,
                        hold_ms=(released - acquired) * 1000,
                        acquired=acquired,
                        released=released,
                    )
                )
            return await self.lock.__aexit__(*args)

    def observed_init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.lock = ObservedLock(self.lock)

    AsyncPostgresSaver.__init__ = observed_init
