"""Process-owned SSE wakeups and bounded shared reads; PostgreSQL is authoritative.

NOTIFY is an invalidation hint, never the event payload. Each process with live
subscribers owns one LISTEN connection. Missed notifications are reconciled by
slow reads, and each HTTP connection retains its own durable sequence cursor.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import json
import logging
import time
from uuid import UUID

import asyncpg
from fastapi import HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.engine import make_url

from api_service.core import database
from api_service.core.database import short_session
from api_service.core.enums import DeleteYN
from api_service.models.common.user_model import UserModel
from api_service.models.common.session_model import SessionModel
from api_service.models.common.project_model import ProjectModel
from api_service.services.public_run_service import PublicRunService, project, TERMINAL
from api_service.services.task_event_service import TaskEventService
from service_runtime.cleanup import protected_cleanup
from service_contracts.run_events import public_event_payload

log = logging.getLogger(__name__)
CHANNEL = 'dtest_run_changed'


@dataclass(eq=False)
class Subscription:
    key: tuple[UUID, UUID, UUID]
    generation: int = 0
    references: int = 0
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    reconcile_at: float = 0
    last_read_at: float = 0
    last_read_generation: int = -1

    def invalidate(self):
        self.generation += 1
        previous, self.changed = self.changed, asyncio.Event()
        previous.set()


@dataclass(frozen=True)
class Frame:
    events: tuple[tuple[int, str], ...]
    state: str
    terminal: bool

    size: int = field(init=False)

    def __post_init__(self):
        object.__setattr__(self, "size", len(self.state.encode()) + sum(len(text.encode()) for _, text in self.events))


class RunStreamHub:
    CACHE_BYTES = 8 * 1024 * 1024
    CACHE_PAGES = 64

    def __init__(self, settings, *, session_factory=None, connect=None):
        self.settings = settings
        self.session_factory = session_factory or (lambda: database.get_session_factory()())
        self.connect = connect or asyncpg.connect
        self.entries = {}
        self.cache = OrderedDict()
        self.cached_bytes = 0
        self.subscribers = 0
        self.listener = None
        self.connection = None
        self.closed = False
        self.lifecycle_lock = asyncio.Lock()
        self.ready = asyncio.Event()

    def invalidate(self, payload):
        for entry in self.entries.values():
            if payload == '*' or str(entry.key[2]) == payload:
                entry.invalidate()
        # Invalidate cached authorization and payloads immediately.
        for key in list(self.cache):
            if payload == '*' or str(key[0][2]) == payload:
                self.cached_bytes -= self.cache.pop(key).size

    async def _listen(self):
        while True:
            conn = None
            try:
                dsn = make_url(self.settings.database_url).set(drivername='postgresql').render_as_string(hide_password=False)
                conn = await self.connect(dsn, timeout=5, ssl=False)
                self.connection = conn
                lost = asyncio.Event()
                conn.add_termination_listener(lambda _: lost.set())
                await conn.add_listener(CHANNEL, lambda _c, _pid, _ch, payload: self.invalidate(payload))
                # Catch commits between the initial read and LISTEN/reconnect.
                self.invalidate('*')
                self.ready.set()
                await lost.wait()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning('run_stream_listener_unavailable error_type=%s', type(exc).__name__)
            finally:
                self.ready.clear()
                self.connection = None
                if conn is not None:
                    try:
                        await protected_cleanup(conn.close(timeout=2))
                    except Exception as exc:
                        log.warning("run_stream_listener_close_failed error_type=%s", type(exc).__name__)
            await asyncio.sleep(1)

    async def _stop_listener(self):
        task, self.listener = self.listener, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    @asynccontextmanager
    async def subscribe(self, user_id, session_id, run_id):
        async with self.lifecycle_lock:
            if self.closed:
                raise HTTPException(503, 'Run stream service is stopping.')
            if self.subscribers >= self.settings.sse_max_connections:
                raise HTTPException(503, 'Run stream capacity exceeded.', headers={'Retry-After':'5'})
            key = (user_id, session_id, run_id)
            entry = self.entries.setdefault(key, Subscription(key, reconcile_at=time.monotonic()+self.settings.sse_reconcile_interval_seconds))
            entry.references += 1
            self.subscribers += 1
            if self.listener is None or self.listener.done():
                self.listener = asyncio.create_task(self._listen(), name='run-stream-listener')
        try:
            yield entry
        finally:
            async def release():
                async with self.lifecycle_lock:
                    entry.references -= 1
                    self.subscribers -= 1
                    if entry.references == 0:
                        self.entries.pop(key, None)
                        for cache_key in list(self.cache):
                            if cache_key[0] == key:
                                self.cached_bytes -= self.cache.pop(cache_key).size
                    if not self.subscribers:
                        await self._stop_listener()
            await protected_cleanup(release())

    def begin_shutdown(self):
        # Called by the root SIGTERM hook before Uvicorn waits for HTTP drain.
        self.closed = True
        self.invalidate('*')

    async def close(self):
        async with self.lifecycle_lock:
            self.begin_shutdown()
            await self._stop_listener()

    async def read(self, entry, sequence):
        # A subscriber can disconnect during pool checkout or a SQL await.
        # Own the entire short DB frame in a separate task: complete/check in
        # its connection before propagating cancellation to the stream owner.
        # Cache/authorization/generation checks remain inside the same frame.
        return await protected_cleanup(self._read_frame(entry, sequence))

    async def _read_frame(self, entry, sequence):
        # Serialize reads for this authorized Run, including simultaneous tabs.
        async with entry.lock:
            generation = entry.generation
            # Coalesce token/status bursts to at most one changed-generation read
            # per legacy poll interval. Cursor pagination is never delayed.
            if generation != entry.last_read_generation:
                delay = entry.last_read_at + self.settings.sse_poll_interval_seconds - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
                generation = entry.generation
            key = (entry.key, generation, sequence)
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key], generation
            entry.last_read_at = time.monotonic()
            entry.last_read_generation = generation
            user_id, session_id, run_id = entry.key
            async with short_session(self.session_factory) as db:
                access = await db.scalar(select(SessionModel.session_id).join(
                    UserModel, UserModel.user_id == SessionModel.user_id).join(
                    ProjectModel, ProjectModel.project_id == SessionModel.project_id).where(
                    SessionModel.session_id == session_id, SessionModel.user_id == user_id,
                    SessionModel.delete_yn == DeleteYN.N, UserModel.delete_yn == DeleteYN.N,
                    ProjectModel.delete_yn == DeleteYN.N, ProjectModel.user_id == user_id))
                if access is None:
                    raise HTTPException(404, 'Session not found.')
                snapshot = (await PublicRunService.snapshots(db, [run_id])).get(run_id)
                if snapshot is None or snapshot[0].session_id != session_id:
                    raise HTTPException(404, 'Run not found.')
                state = project(*snapshot)
                events = await TaskEventService.list_after_public_run(db, run_id=run_id,
                    sequence=sequence, limit=self.settings.sse_event_batch_size)
                public_events = [(event.sequence, public_event_payload(event, session_id=session_id, run_id=run_id)) for event in events]
                chunks = tuple((sequence,
                    f'id: {sequence}\nevent: {payload["type"]}\ndata: ' +
                    json.dumps(payload, ensure_ascii=False, separators=(',',':'))+'\n\n') for sequence, payload in public_events)
                frame = Frame(chunks, state.model_dump_json(), state.status in TERMINAL)
            size = frame.size
            if generation == entry.generation and size <= self.CACHE_BYTES:
                self.cache[key] = frame
                self.cached_bytes += size
                while len(self.cache) > self.CACHE_PAGES or self.cached_bytes > self.CACHE_BYTES:
                    _, expired = self.cache.popitem(last=False)
                    self.cached_bytes -= expired.size
            return frame, generation

    async def wait(self, entry, generation, timeout):
        if self.closed or entry.generation != generation:
            return
        event = entry.changed
        remaining = max(0, entry.reconcile_at - time.monotonic())
        try:
            await asyncio.wait_for(event.wait(), timeout=min(timeout, remaining))
        except TimeoutError:
            if time.monotonic() >= entry.reconcile_at:
                entry.reconcile_at = time.monotonic()+self.settings.sse_reconcile_interval_seconds
                self.invalidate(str(entry.key[2]))

    async def stream(self, request, entry, sequence):
        previous_state = None
        last_heartbeat = time.monotonic()
        generation = -1
        while not self.closed and not await request.is_disconnected():
            if generation != entry.generation:
                frame, generation = await self.read(entry, sequence)
                for sequence, text in frame.events:
                    yield text
                full = len(frame.events) >= self.settings.sse_event_batch_size
                if not full and frame.state != previous_state:
                    previous_state = frame.state
                    snapshot = {'schema_version': 1, 'type': 'run.snapshot',
                        'session_id': str(entry.key[1]), 'run_id': str(entry.key[2]), 'cursor': sequence,
                        'data': json.loads(frame.state)}
                    yield 'event: run.snapshot\ndata: ' + json.dumps(snapshot, ensure_ascii=False, separators=(',', ':')) + '\n\n'
                if frame.terminal and not frame.events:
                    return
                if full or frame.terminal:
                    generation = -1  # Drain durable backlog immediately, no poll delay.
                    continue
            now = time.monotonic()
            if now-last_heartbeat >= self.settings.sse_heartbeat_seconds:
                yield ': heartbeat\n\n'
                last_heartbeat = now
            # Disconnect/heartbeat checks do not perform SQL. Starlette also cancels
            # the iterator on disconnect; the short wake is for direct consumers.
            await self.wait(entry, generation, min(self.settings.sse_poll_interval_seconds,
                self.settings.sse_heartbeat_seconds))


class RunStreamResponse(StreamingResponse):
    """Reserve capacity before HTTP headers, release even on disconnect/send failure."""
    def __init__(self, hub, request, key, sequence):
        super().__init__(iter(()), media_type='text/event-stream',
            headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})
        self.hub, self.request, self.key, self.sequence = hub, request, key, sequence

    async def __call__(self, scope, receive, send):
        owner = self.hub.subscribe(*self.key)
        try:
            entry = await owner.__aenter__()
        except HTTPException as exc:
            return await JSONResponse({'detail':exc.detail}, status_code=exc.status_code,
                headers=exc.headers)(scope, receive, send)
        iterator = self.hub.stream(self.request, entry, self.sequence)
        self.body_iterator = iterator
        try:
            await super().__call__(scope, receive, send)
        finally:
            async def cleanup():
                try:
                    await iterator.aclose()
                finally:
                    await owner.__aexit__(None, None, None)
            await protected_cleanup(cleanup())
