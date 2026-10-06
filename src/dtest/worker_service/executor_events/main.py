"""Executor event ingestion/routing only; graph calls belong to Agent Worker."""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Callable

from dtest.application.runs.lifecycle import execution_health
from dtest.worker_service.executor_events import ExecutorWorker
from dtest.worker_service.executor_events.event_types import EVENT_TYPES


async def main(
    *,
    install_signals: bool = True,
    stop_event=None,
    on_worker: Callable | None = None,
) -> None:
    from dtest.settings.loader import get_settings

    # Registry keys route events into the DB ledger; no graph callback is invoked here.
    async with ExecutorWorker(get_settings().worker, EVENT_TYPES) as worker:

        async def execution_ready():
            return execution_health.healthy

        worker.add_readiness_check("command-worker", execution_ready)
        loop = asyncio.get_running_loop()
        installed = []
        if install_signals:
            for signum in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(signum, worker.request_stop)
                installed.append(signum)
        if on_worker is not None:
            on_worker(worker)
        try:
            await worker.run(stop_event=stop_event)
        finally:
            if on_worker is not None:
                on_worker(None)
            for signum in installed:
                loop.remove_signal_handler(signum)


def run_worker() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())


if __name__ == "__main__":
    run_worker()
