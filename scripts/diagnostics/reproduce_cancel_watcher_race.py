"""Offline reproduction of Run invocation cleanup waiting for a canceled watcher.

Uses the installed SQLAlchemy connection-pool queue and the actual Run monitoring
cleanup implementation. The DB query is replaced by queue acquisition; no
database/network calls or application mutations are performed.
"""

import asyncio
import json
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from dtest.application.runs import monitoring
from dtest.application.runs.monitoring import run_cancellable

from sqlalchemy.util.queue import AsyncAdaptedQueue
from sqlalchemy.util.concurrency import greenlet_spawn


async def trial(extra_yield):
    queue = AsyncAdaptedQueue()
    ready = asyncio.Event()
    watcher_ref = []
    graph_completed = []

    async def watcher(_run_id, stop):
        watcher_ref.append(asyncio.current_task())
        ready.set()
        # AsyncAdaptedQueue.get uses asyncio.wait_for for pool_timeout.
        await greenlet_spawn(queue.get, True, 30)
        # Stand-in for the next iteration of the real cancellation polling loop.
        await stop.wait()
        return False

    async def graph():
        await ready.wait()
        queue.put_nowait(None)
        if extra_yield:
            await asyncio.sleep(0)
        graph_completed.append(True)
        return {"completed": True}

    with patch.object(monitoring, "wait_for_cancellation", watcher):
        run = asyncio.create_task(
            run_cancellable("offline-reproduction", graph())
        )
        done, _ = await asyncio.wait([run], timeout=0.05)
        result = {
            "extra_yield": extra_yield,
            "graph_completed": bool(graph_completed),
            "run_returned": bool(done),
            "watcher_done": watcher_ref[0].done(),
            "watcher_cancellation_requests": watcher_ref[0].cancelling(),
        }
        if not done:
            # Stop this isolated reproduction; do not leave a background task.
            run.cancel()
        try:
            await run
        except asyncio.CancelledError:
            pass
        assert watcher_ref[0].done()
        return result


async def main():
    race = [await trial(False) for _ in range(20)]
    control = [await trial(True) for _ in range(20)]
    result = {
        "race": race,
        "control": control,
        "summary": {
            "race_stalled": sum(not r["run_returned"] for r in race),
            "race_trials": len(race),
            "control_stalled": sum(not r["run_returned"] for r in control),
            "control_trials": len(control),
        },
    }
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(json.dumps(result, indent=2))
    print(json.dumps(result["summary"]))


if __name__ == "__main__":
    asyncio.run(main())
