"""One-time, idempotent admission of legacy work after old dispatchers stop.

Uses the configured common database and namespace. Never claims, clears owner
or replays a graph. Separate Event DB contents must be copied before this step.
"""
from sqlalchemy import text

from api_service.core.database import get_session_factory
from service_settings import get_settings


async def backfill(factory=None) -> int:
    factory = factory or get_session_factory()
    namespace = get_settings().worker.namespace
    async with factory() as db:
        if await db.scalar(text("SELECT EXISTS (SELECT 1 FROM agent_commands WHERE state IN ('RUNNING','RECOVERY'))")):
            raise RuntimeError("Existing command ownership must be drained or recovered first")
        if await db.scalar(text("SELECT EXISTS (SELECT 1 FROM session_executions WHERE token IS NOT NULL OR recovery_required)")):
            raise RuntimeError("Stop/recover previous graph owners before command migration")
        if await db.scalar(text("SELECT EXISTS (SELECT 1 FROM agent_runs WHERE status='running')")):
            raise RuntimeError("Previous RUNNING invocations require confirmed termination and recovery")
        if await db.scalar(text("SELECT EXISTS (SELECT 1 FROM ew_commands WHERE namespace=:ns AND state='RUNNING')"), {"ns":namespace}):
            raise RuntimeError("Previous RUNNING event commands require recovery before migration")
        if await db.scalar(text("""SELECT EXISTS (SELECT 1 FROM ew_commands c
            LEFT JOIN ew_bindings b ON b.namespace=c.namespace AND b.execution_id=c.execution_id
            LEFT JOIN sessions s ON s.session_id::text=b.session_id
            WHERE c.namespace=:ns AND c.state IN ('READY','FAILED') AND s.session_id IS NULL)"""), {"ns":namespace}):
            raise RuntimeError("Legacy event command has no API session in the common database")
        # Retrying a completed backfill is safe. Admitting missing old work
        # after new work in the same session would allocate a later ordinal.
        if await db.scalar(text("""WITH missing AS (
            SELECT r.session_id FROM agent_runs r WHERE r.status='pending'
            AND NOT EXISTS (SELECT 1 FROM agent_commands c
                WHERE c.namespace=:ns AND c.invocation_id=r.run_id)
            UNION ALL
            SELECT s.session_id FROM ew_commands e JOIN ew_bindings b USING(namespace,execution_id)
            JOIN sessions s ON s.session_id::text=b.session_id
            WHERE e.namespace=:ns AND e.state='READY'
            AND NOT EXISTS (SELECT 1 FROM agent_commands c
                WHERE c.namespace=e.namespace AND c.command_id=e.command_id)
        ) SELECT EXISTS (SELECT 1 FROM missing m JOIN agent_commands c USING(session_id)
            WHERE c.state NOT IN ('DONE','IGNORED','FAILED'))"""), {"ns":namespace}):
            raise RuntimeError("Mixed legacy/new session commands require ordered migration before serving")
        result = await db.execute(text("""INSERT INTO agent_commands
            (namespace,command_id,session_id,kind,invocation_id,payload,state,available_at,created_at,failure_attempts,last_error)
            SELECT :ns,id,session_id,kind,invocation_id,payload,state,available_at,created_at,failure_attempts,last_error FROM (
                SELECT r.run_id AS id,r.session_id,
                    CASE WHEN r.command IS NULL OR r.command='null'::jsonb THEN 'user_start' ELSE 'user_resume' END AS kind,
                    r.run_id AS invocation_id,NULL::jsonb AS payload,'READY' AS state,
                    coalesce(r.next_attempt_at,now()) AS available_at,r.created_at,
                    0 AS failure_attempts,NULL::text AS last_error
                FROM agent_runs r WHERE r.status='pending'
                UNION ALL
                SELECT c.command_id,s.session_id,'executor_resume',NULL::uuid,
                    jsonb_build_object('task_id',b.task_id,'execution_id',c.execution_id::text,'event',i.event),
                    c.state,now(),c.created_at,c.failure_attempts,c.last_error
                FROM ew_commands c JOIN ew_bindings b USING(namespace,execution_id)
                JOIN ew_inbox i ON i.namespace=c.namespace AND i.event_id=c.event_id
                JOIN sessions s ON s.session_id::text=b.session_id
                WHERE c.namespace=:ns AND c.state IN ('READY','FAILED')
            ) legacy ORDER BY created_at,id
            ON CONFLICT (namespace,command_id) DO NOTHING"""), {"ns":namespace})
        await db.commit()
        return result.rowcount


async def main():
    try:
        print(f"legacy_commands_admitted={await backfill()}")
    finally:
        from api_service.core.database import close_database
        await close_database()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
