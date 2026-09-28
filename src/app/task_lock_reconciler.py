"""Flag stale execution ownership for recovery without releasing session locks."""

from __future__ import annotations

import argparse
import asyncio

from config import settings
from app.core.database import get_session_factory
from app.services.task_service import TaskService


async def reconcile_once() -> int:
    """만료된 실행 lease를 복구 필요로 표시하고 처리 개수를 반환합니다.

    이전 writer의 종료/fencing 보장이 없으므로 자동 재실행하거나 세션 잠금을 풀지 않습니다.
    """

    async with get_session_factory()() as db:
        recovered = await TaskService.reconcile_stale(db)
        return len(recovered)


async def run_forever() -> None:
    """E03-T06: 설정된 주기로 stale lease를 감지합니다."""

    while True:
        await reconcile_once()
        await asyncio.sleep(settings.task_reconcile_interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Flag stale Task execution ownership")
    parser.add_argument(
        "--once",
        action="store_true",
        help="한 batch만 표시하고 종료합니다.",
    )
    args = parser.parse_args()
    if args.once:
        recovered_count = asyncio.run(reconcile_once())
        print(f"recovery_required_tasks={recovered_count}")
        return
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
