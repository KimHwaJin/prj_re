from __future__ import annotations

from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from dtest.contracts.events import ExecutorEvent


class Store:
    """Executor Inbox/bindings and atomic admission into the common command ledger."""

    def __init__(self, pool: AsyncConnectionPool, namespace: str) -> None:
        self.pool = pool
        self.namespace = namespace

    async def register(
        self,
        *,
        execution_id: UUID,
        session_id: str,
        task_id: str,
        actor: str = "api",
    ) -> None:
        if not session_id or not task_id or not actor:
            raise ValueError("session_id, task_id and actor are required")
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute(
                """INSERT INTO ew_bindings
                (namespace, execution_id, session_id, task_id,
                 created_by, updated_by) VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING""",
                (
                    self.namespace,
                    execution_id,
                    session_id,
                    task_id,
                    actor,
                    actor,
                ),
            )
            cur = await conn.execute(
                """SELECT session_id, task_id FROM ew_bindings
                WHERE namespace=%s AND execution_id=%s""",
                (self.namespace, execution_id),
            )
            if await cur.fetchone() != (session_id, task_id):
                raise ValueError("Execution binding is immutable")

        # Wake only after transaction commit and connection return. A very fast
        # Executor may have published all events before this binding existed.
        from dtest.infrastructure.database.binding_signals import binding_committed
        binding_committed(self.pool.conninfo, self.namespace)

    async def ingest(
        self,
        event: ExecutorEvent,
        *,
        catch_up: bool = False,
    ) -> None:
        data = event.model_dump(mode="json")
        identity = event.identity_document()
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute(
                """INSERT INTO ew_inbox
                (namespace,event_id,execution_id,sequence,event,
                 created_by,updated_by) VALUES (%s,%s,%s,%s,%s,'worker',
                 'worker') ON CONFLICT DO NOTHING""",
                (
                    self.namespace,
                    event.event_id,
                    event.execution_id,
                    event.event_sequence,
                    Jsonb(data),
                ),
            )
            cur = await conn.execute(
                """SELECT event FROM ew_inbox
                WHERE namespace=%s AND event_id=%s""",
                (self.namespace, event.event_id),
            )
            row = await cur.fetchone()
            if row is None or (
                ExecutorEvent.model_validate(row[0]).identity_document()
                != identity
            ):
                raise ValueError("Conflicting event identity or sequence")
            if catch_up:
                await conn.execute(
                    """UPDATE ew_bindings SET
                    catch_up_version=catch_up_version+1,updated_at=now(),
                    updated_by='worker' WHERE namespace=%s
                    AND execution_id=%s""",
                    (self.namespace, event.execution_id),
                )

    async def finish_catch_up(self, execution_id: UUID, version: int) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                """UPDATE ew_bindings SET
                caught_up_version=greatest(caught_up_version,%s),
                updated_at=now(),updated_by='worker'
                WHERE namespace=%s AND execution_id=%s""",
                (version, self.namespace, execution_id),
            )

    async def scan_candidates(self, limit: int) -> list[dict[str, Any]]:
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """SELECT b.* FROM ew_bindings b
                    WHERE b.namespace=%s AND b.next_scan_at<=now()
                    AND (b.catch_up_version>b.caught_up_version
                    OR EXISTS (SELECT 1 FROM ew_inbox i
                        WHERE i.namespace=b.namespace
                        AND i.execution_id=b.execution_id
                        AND i.sequence>b.last_sequence))
                    ORDER BY b.next_scan_at, b.execution_id LIMIT %s""",
                    (self.namespace, limit),
                )
                return await cur.fetchall()

    async def scan_error(self, execution_id: UUID, error: str) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                """UPDATE ew_bindings SET last_error=%s,
                next_scan_at=now()+interval '5 seconds',
                updated_at=now(),updated_by='worker'
                WHERE namespace=%s AND execution_id=%s""",
                (error[:2000], self.namespace, execution_id),
            )

    async def advance(
        self,
        execution_id: UUID,
        event_types: set[str],
        limit: int,
    ) -> tuple[int, int | None]:
        """Atomically route a contiguous Inbox prefix into the graph command ledger.

        Returns (number advanced, gap-after-sequence or None).
        """
        async with self.pool.connection() as conn, conn.transaction():
            binding = await conn.execute(
                "SELECT session_id FROM ew_bindings WHERE namespace=%s AND execution_id=%s",
                (self.namespace, execution_id),
            )
            identity = await binding.fetchone()
            if identity is None:
                return 0, None
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                               (f"agent-command-order:{UUID(identity[0])}",))
            cur = await conn.execute(
                """SELECT last_sequence,session_id,task_id FROM ew_bindings
                WHERE namespace=%s AND execution_id=%s FOR UPDATE""",
                (self.namespace, execution_id),
            )
            row = await cur.fetchone()
            if row is None:
                return 0, None
            sequence = row[0]
            cur = await conn.execute(
                """SELECT event FROM ew_inbox WHERE namespace=%s
                AND execution_id=%s AND sequence>%s
                ORDER BY sequence LIMIT %s""",
                (self.namespace, execution_id, sequence, limit),
            )
            events = await cur.fetchall()
            count = 0
            gap = None
            for (data,) in events:
                event = ExecutorEvent.model_validate(data)
                if event.event_sequence != sequence + 1:
                    gap = sequence
                    break
                routed = event.event_type in event_types
                if routed:
                    command_id = uuid5(
                        NAMESPACE_URL,
                        f"{self.namespace}/event/{event.event_id}",
                    )
                    await conn.execute(
                        """INSERT INTO agent_commands
                        (namespace,command_id,session_id,kind,payload)
                        VALUES (%s,%s,%s,'executor_resume',%s)""",
                        (self.namespace, command_id, UUID(row[1]), Jsonb({
                            "task_id": row[2], "execution_id": str(execution_id),
                            "event": data,
                        })),
                    )
                await conn.execute(
                    """UPDATE ew_inbox SET state=%s,updated_at=now(),
                    updated_by='worker' WHERE namespace=%s AND event_id=%s""",
                    (
                        "ROUTED" if routed else "IGNORED",
                        self.namespace,
                        event.event_id,
                    ),
                )
                sequence = event.event_sequence
                count += 1
            await conn.execute(
                """UPDATE ew_bindings SET last_sequence=%s,
                next_scan_at=now(),last_error=NULL,updated_at=now(),
                updated_by='worker' WHERE namespace=%s AND execution_id=%s""",
                (sequence, self.namespace, execution_id),
            )
            return count, gap

    async def counts(self) -> dict[str, int]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """SELECT 'command:'||state,count(*) FROM agent_commands
                WHERE namespace=%s GROUP BY state UNION ALL
                SELECT 'inbox:'||state,count(*) FROM ew_inbox
                WHERE namespace=%s GROUP BY state""",
                (self.namespace,) * 2,
            )
            return dict(await cur.fetchall())
