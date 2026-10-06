"""Bounded streaming callback buffer; commit token events before Run completion."""
from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler

from config import settings
from api_service.infrastructure.database import get_session_factory, short_session
from api_service.runs.lifecycle import finish_observer
from service_contracts.execution import ExecutionNeedsRecovery
from service_runtime.cleanup import protected_cleanup
from api_service.runs.task_events import TaskEventService


class TokenEventBufferError(ExecutionNeedsRecovery):
    """Incomplete token persistence must not become success or a graph retry."""


@dataclass(frozen=True)
class _Chunk:
    llm_run_id: str  # LangChain callback ID; not a relational llm_runs row.
    text: str
    size: int
    admitted_at: float


class LLMTokenEventBuffer(AsyncCallbackHandler):
    """One Run owns a bounded queue, one writer and ordered callback admission.

    Payload accounting includes queued, batched and in-flight writes until commit.
    The limit excludes the SDK's input string, Python objects and temporary joins.
    """

    # LangChain must await backpressure and propagate persistence failures.
    run_inline = True
    raise_error = True

    def __init__(self, *, task_id: UUID, run_id: UUID, expose_tokens: bool = True) -> None:
        self.expose_tokens = expose_tokens
        self.task_id = task_id
        self.run_id = run_id
        self.max_bytes = settings.llm_token_buffer_max_bytes
        self.max_items = settings.llm_token_buffer_max_items
        self.interval = settings.llm_token_flush_interval_seconds
        self.flush_characters = settings.llm_token_flush_characters
        self.enqueue_timeout = settings.llm_token_enqueue_timeout_seconds
        self.write_timeout = settings.llm_token_write_timeout_seconds
        # A Unicode character takes at most four UTF-8 bytes. Split lazily so one
        # unusually large SDK chunk cannot exceed the buffer admission budget.
        self.chunk_characters = min(self.flush_characters, self.max_bytes // 4)
        self.queue: deque[_Chunk] = deque()
        self.buffered_bytes = 0
        self.buffered_items = 0
        self.peak_buffered_bytes = 0
        self.peak_buffered_items = 0
        self.offsets: dict[str, int] = defaultdict(int)
        self.consumer: asyncio.Task | None = None
        self.closed = False
        self.failure: BaseException | None = None
        self._changed = asyncio.Condition()
        self._producer = asyncio.Lock()
        self._waiting_for_capacity = False

    def _check_accepting(self):
        if self.failure is not None:
            raise TokenEventBufferError("Token event writer failed.") from self.failure
        if self.closed or (self.consumer is not None and self.consumer.done()):
            raise TokenEventBufferError("Token event buffer is closed.")
        if self.consumer is None:
            raise RuntimeError("Token event buffer has not been started.")

    async def on_llm_new_token(self, token: str, *, run_id, **_kwargs) -> None:
        if not self.expose_tokens:
            return
        if not token:
            return
        self._check_accepting()
        try:
            async with asyncio.timeout(self.enqueue_timeout):
                # Serialize callback fragments, including split oversized tokens.
                async with self._producer:
                    for offset in range(0, len(token), self.chunk_characters):
                        text = token[offset:offset + self.chunk_characters]
                        size = len(text.encode("utf-8"))
                        async with self._changed:
                            self._check_accepting()
                            try:
                                while (self.buffered_items >= self.max_items or
                                       self.buffered_bytes + size > self.max_bytes):
                                    self._waiting_for_capacity = True
                                    self._changed.notify_all()
                                    await self._changed.wait()
                                    self._check_accepting()
                            finally:
                                self._waiting_for_capacity = False
                            self.queue.append(_Chunk(str(run_id), text, size, asyncio.get_running_loop().time()))
                            self.buffered_bytes += size
                            self.buffered_items += 1
                            self.peak_buffered_bytes = max(self.peak_buffered_bytes, self.buffered_bytes)
                            self.peak_buffered_items = max(self.peak_buffered_items, self.buffered_items)
                            self._changed.notify_all()
        except TimeoutError as exc:
            # Wake/stop both admission and the observer. Callback errors alone can
            # otherwise be swallowed by a model wrapper and leave a partial Run.
            if self.failure is None:
                self.failure = TokenEventBufferError("Token event admission timed out.")
            if self.consumer is not None and not self.consumer.done() and not self.consumer.cancelling():
                self.consumer.cancel()
            async with self._changed:
                self._changed.notify_all()
            raise TokenEventBufferError("Token event admission timed out.") from exc

    async def _append(self, llm_run_id: str, delta: str) -> None:
        start = self.offsets[llm_run_id]
        end = start + len(delta)
        async with short_session(get_session_factory()) as db:
            await TaskEventService.append(
                db, task_id=self.task_id, run_id=self.run_id,
                event_type="llm.token.delta",
                payload={"llm_run_id": llm_run_id, "delta": delta,
                         "offset_start": start, "offset_end": end},
            )
        self.offsets[llm_run_id] = end

    async def _flush(self, pending: list[_Chunk]) -> None:
        grouped: dict[str, list[str]] = defaultdict(list)
        for chunk in pending:
            grouped[chunk.llm_run_id].append(chunk.text)
        for llm_run_id, chunks in grouped.items():
            # Includes pool acquisition, SQL, commit and connection return.
            async with asyncio.timeout(self.write_timeout):
                await self._append(llm_run_id, "".join(chunks))
        async with self._changed:
            self.buffered_bytes -= sum(chunk.size for chunk in pending)
            self.buffered_items -= len(pending)
            self._changed.notify_all()

    async def _consume(self) -> None:
        pending: list[_Chunk] = []
        characters = 0
        deadline = None
        loop = asyncio.get_running_loop()
        try:
            while True:
                async with self._changed:
                    while not self.queue and not self.closed and self.failure is None:
                        if pending and self._waiting_for_capacity:
                            break
                        if deadline is None:
                            await self._changed.wait()
                        else:
                            remaining = deadline - loop.time()
                            if remaining <= 0:
                                break
                            try:
                                async with asyncio.timeout(remaining):
                                    await self._changed.wait()
                            except TimeoutError:
                                break
                    if self.failure is not None:
                        raise self.failure
                    if self.queue:
                        chunk = self.queue.popleft()
                        pending.append(chunk)
                        characters += len(chunk.text)
                        if deadline is None:
                            deadline = chunk.admitted_at + self.interval
                    flush = bool(pending) and (
                        characters >= self.flush_characters or
                        (not self.queue and (self._waiting_for_capacity or
                         self.buffered_items >= self.max_items or self.buffered_bytes >= self.max_bytes)) or
                        loop.time() >= deadline or (self.closed and not self.queue)
                    )
                    finished = self.closed and not self.queue and not pending
                if finished:
                    return
                if flush:
                    await self._flush(pending)
                    pending.clear()
                    characters = 0
                    deadline = None
        except BaseException as exc:
            if self.failure is None:
                self.failure = exc
            raise
        finally:
            # Only release accounting after the writer has actually stopped.
            # Failed, uncommitted fragments are not reported as persisted.
            async with self._changed:
                self.queue.clear()
                self.buffered_bytes = self.buffered_items = 0
                self._changed.notify_all()

    def start(self) -> None:
        if self.consumer is not None or self.closed:
            raise RuntimeError("Token event buffer cannot be restarted.")
        self.consumer = asyncio.create_task(self._consume(), name=f"llm-token-events:{self.run_id}")

    async def close(self) -> None:
        # No sentinel: shutdown must work even when every buffer slot is occupied.
        self.closed = True
        async def finish():
            async with self._changed:
                self._changed.notify_all()
            if self.consumer is not None:
                await finish_observer(self.consumer, run_id=self.run_id, stage="token_flush_stop")
        await protected_cleanup(finish())
