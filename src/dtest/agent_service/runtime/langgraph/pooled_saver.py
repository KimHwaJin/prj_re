"""Use one existing pool without an instance-wide checkpoint I/O queue.

checkpoint-postgres 3.1.2 locks each saver even when its connection source is
an AsyncConnectionPool. A short-lived official saver per operation keeps its
connection/pipeline lock while independent operations borrow separate pool
connections. It creates no pools or connections outside that pool, changes no SQL
and uses the same serializer, schema and checkpoint format.

Session/command ownership remains the application's responsibility. This
adapter does not authorize concurrent graph writers to the same thread.
"""
from collections.abc import AsyncIterator, Sequence
from contextlib import aclosing
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    ChannelVersions, Checkpoint, CheckpointMetadata, CheckpointTuple,
    DeltaChannelHistory,
)
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool


class PooledAsyncPostgresSaver(AsyncPostgresSaver):
    """Bound concurrent database access by the existing pool's max_size.

    setup() uses the inherited saver before serving starts. Data operations
    delegate through public APIs only; library cursor/SQL internals are intact.
    Sync entry points inherited from AsyncPostgresSaver call these async APIs.
    """
    def __init__(self, pool: AsyncConnectionPool, *, serde=None):
        if not isinstance(pool, AsyncConnectionPool):
            raise TypeError('PooledAsyncPostgresSaver requires an AsyncConnectionPool')
        super().__init__(pool, serde=serde)

    def _operation(self) -> AsyncPostgresSaver:
        return AsyncPostgresSaver(self.conn, serde=self.serde)

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return await self._operation().aget_tuple(config)

    async def aput(self, config: RunnableConfig, checkpoint: Checkpoint,
                   metadata: CheckpointMetadata, new_versions: ChannelVersions) -> RunnableConfig:
        return await self._operation().aput(config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config: RunnableConfig, writes: Sequence[tuple[str, Any]],
                          task_id: str, task_path: str = '') -> None:
        await self._operation().aput_writes(config, writes, task_id, task_path)

    async def alist(self, config: RunnableConfig | None, *, filter: dict[str, Any] | None = None,
                    before: RunnableConfig | None = None, limit: int | None = None) -> AsyncIterator[CheckpointTuple]:
        async with aclosing(self._operation().alist(
            config, filter=filter, before=before, limit=limit,
        )) as history:
            async for item in history:
                yield item

    async def adelete_thread(self, thread_id: str) -> None:
        await self._operation().adelete_thread(thread_id)

    async def aget_delta_channel_history(self, *, config: RunnableConfig,
                                        channels: Sequence[str]) -> dict[str, DeltaChannelHistory]:
        return await self._operation().aget_delta_channel_history(config=config, channels=channels)
