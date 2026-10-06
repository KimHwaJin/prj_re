"""Retire duplicate Worker storage and the disconnected old Workflow store.

The current API Workflow catalog/embeddings are separate active tables.
Downgrade restores empty legacy tables only, never deleted catalog contents.
"""
from alembic import op
from sqlalchemy import text

revision = "ew_0003"
down_revision = "ew_0002"
branch_labels = None
depends_on = None


def _admit_legacy_work(conn, namespace):
    """Preserve queued work once, before deleting the duplicate legacy ledger.

    All event namespaces are included. New user work is admitted only when no
    current invocation command exists. Old/new writers must be stopped first.
    """
    if conn.scalar(text("SELECT EXISTS (SELECT 1 FROM agent_commands WHERE state IN ('RUNNING','RECOVERY'))")):
        raise RuntimeError("Existing command ownership must be drained or recovered first")
    if conn.scalar(text("SELECT EXISTS (SELECT 1 FROM session_executions WHERE token IS NOT NULL OR recovery_required)")):
        raise RuntimeError("Stop/recover previous graph owners before command migration")
    if conn.scalar(text("SELECT EXISTS (SELECT 1 FROM agent_runs WHERE status='running')")):
        raise RuntimeError("Previous RUNNING invocations require confirmed termination and recovery")
    if conn.scalar(text("SELECT EXISTS (SELECT 1 FROM ew_commands WHERE state='RUNNING')"), {"ns":namespace}):
        raise RuntimeError("Previous RUNNING event commands require recovery before migration")
    if conn.scalar(text("""SELECT EXISTS (SELECT 1 FROM ew_commands c
        LEFT JOIN ew_bindings b ON b.namespace=c.namespace AND b.execution_id=c.execution_id
        LEFT JOIN sessions s ON s.session_id::text=b.session_id
        WHERE c.state IN ('READY','FAILED') AND (s.session_id IS NULL OR NOT EXISTS (SELECT 1 FROM ew_inbox i WHERE i.namespace=c.namespace AND i.event_id=c.event_id)))"""), {"ns":namespace}):
        raise RuntimeError("Legacy event command has no API session in the common database")
    # Retrying a completed backfill is safe. Admitting missing old work
    # after new work in the same session would allocate a later ordinal.
    if conn.scalar(text("""WITH missing AS (
        SELECT r.session_id FROM agent_runs r WHERE r.status='pending'
        AND NOT EXISTS (SELECT 1 FROM agent_commands c
            WHERE c.invocation_id=r.run_id)
        UNION ALL
        SELECT s.session_id FROM ew_commands e JOIN ew_bindings b USING(namespace,execution_id)
        JOIN sessions s ON s.session_id::text=b.session_id
        WHERE e.state='READY'
        AND NOT EXISTS (SELECT 1 FROM agent_commands c
            WHERE c.namespace=e.namespace AND c.command_id=e.command_id)
    ) SELECT EXISTS (SELECT 1 FROM missing m JOIN agent_commands c USING(session_id)
        WHERE c.state NOT IN ('DONE','IGNORED','FAILED'))"""), {"ns":namespace}):
        raise RuntimeError("Mixed legacy/new session commands require ordered migration before serving")
    result = conn.execute(text("""INSERT INTO agent_commands
        (namespace,command_id,session_id,kind,invocation_id,payload,state,available_at,created_at,failure_attempts,last_error)
        SELECT namespace,id,session_id,kind,invocation_id,payload,state,available_at,created_at,failure_attempts,last_error FROM (
            SELECT :ns AS namespace,r.run_id AS id,r.session_id,
                CASE WHEN r.command IS NULL OR r.command='null'::jsonb THEN 'user_start' ELSE 'user_resume' END AS kind,
                r.run_id AS invocation_id,NULL::jsonb AS payload,'READY' AS state,
                coalesce(r.next_attempt_at,now()) AS available_at,r.created_at,
                0 AS failure_attempts,NULL::text AS last_error
            FROM agent_runs r WHERE r.status='pending' AND NOT EXISTS (SELECT 1 FROM agent_commands a WHERE a.invocation_id=r.run_id)
            UNION ALL
            SELECT c.namespace,c.command_id,s.session_id,'executor_resume',NULL::uuid,
                jsonb_build_object('task_id',b.task_id,'execution_id',c.execution_id::text,'event',i.event),
                c.state,now(),c.created_at,c.failure_attempts,c.last_error
            FROM ew_commands c JOIN ew_bindings b USING(namespace,execution_id)
            JOIN ew_inbox i ON i.namespace=c.namespace AND i.event_id=c.event_id
            JOIN sessions s ON s.session_id::text=b.session_id
            WHERE c.state IN ('READY','FAILED')
        ) legacy ORDER BY created_at,id
        ON CONFLICT (namespace,command_id) DO NOTHING"""), {"ns":namespace})
    return result.rowcount


def upgrade():
    from service_settings import get_settings
    _admit_legacy_work(op.get_bind(), get_settings().worker.namespace)
    op.execute("DROP VIEW workflow_adaptive_history_view")
    for name in ("ew_outbox", "ew_audit", "ew_commands", "workflow_adaptive_history", "workflow_executions", "workflow_catalog"):
        op.drop_table(name)


def downgrade():
    op.execute("""
    CREATE TABLE workflow_catalog (
    catalog_id UUID NOT NULL,
    workflow_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    source_session_id TEXT NOT NULL,
    source_task_id TEXT NOT NULL,
    source_execution_id UUID,
    intent TEXT NOT NULL,
    workflow_status TEXT NOT NULL,
    workflow JSONB NOT NULL,
    reusable BOOLEAN DEFAULT true NOT NULL,
    validation_status TEXT DEFAULT 'unverified' NOT NULL,
    success_count INTEGER DEFAULT 0 NOT NULL,
    failure_count INTEGER DEFAULT 0 NOT NULL,
    last_execution_status TEXT,
    last_executed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT workflow_catalog_pkey PRIMARY KEY (catalog_id),
    CONSTRAINT workflow_catalog_source_revision_key UNIQUE (source_task_id, workflow_id, revision),
    CONSTRAINT workflow_catalog_validation_status_check CHECK (validation_status IN ('unverified', 'validated', 'failed', 'deprecated')),
    CONSTRAINT workflow_catalog_execution_counts_check CHECK (success_count >= 0 AND failure_count >= 0)
    )
    """)
    op.execute('CREATE INDEX workflow_catalog_recommendation_lookup ON workflow_catalog (intent, reusable, validation_status)')
    op.execute("""
    CREATE TABLE workflow_executions (
    execution_id UUID NOT NULL,
    catalog_id UUID,
    workflow_id TEXT NOT NULL,
    workflow_revision INTEGER NOT NULL,
    session_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    original_workflow JSONB NOT NULL,
    final_workflow JSONB,
    execution_status TEXT DEFAULT 'running' NOT NULL,
    started_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    finished_at TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT workflow_executions_pkey PRIMARY KEY (execution_id),
    CONSTRAINT workflow_executions_catalog_id_fkey FOREIGN KEY(catalog_id) REFERENCES workflow_catalog (catalog_id) ON DELETE SET NULL
    )
    """)
    op.execute('CREATE INDEX workflow_executions_task_lookup ON workflow_executions (session_id, task_id)')
    op.execute("""
    CREATE TABLE workflow_adaptive_history (
    execution_id UUID NOT NULL,
    decision_round INTEGER NOT NULL,
    changes JSONB NOT NULL,
    effective_workflow JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT workflow_adaptive_history_pkey PRIMARY KEY (execution_id, decision_round),
    CONSTRAINT workflow_adaptive_history_execution_id_fkey FOREIGN KEY(execution_id) REFERENCES workflow_executions (execution_id) ON DELETE CASCADE,
    CONSTRAINT workflow_adaptive_history_round_check CHECK (decision_round > 0)
    )
    """)
    op.execute("""
    CREATE VIEW workflow_adaptive_history_view AS
    SELECT
    h.execution_id,
    h.decision_round,
    e.catalog_id,
    e.workflow_id,
    e.workflow_revision,
    e.session_id,
    e.task_id,
    e.original_workflow,
    h.changes,
    h.effective_workflow,
    e.final_workflow,
    e.execution_status,
    e.started_at,
    h.created_at AS decision_created_at,
    e.finished_at
    FROM workflow_adaptive_history h
    JOIN workflow_executions e
    ON e.execution_id = h.execution_id
    """)

    op.execute("""
    CREATE TABLE ew_commands (
    namespace TEXT NOT NULL,
    command_id UUID NOT NULL,
    event_id UUID NOT NULL,
    execution_id UUID NOT NULL,
    sequence BIGINT NOT NULL,
    state TEXT DEFAULT 'READY' NOT NULL,
    failure_attempts INTEGER DEFAULT 0 NOT NULL,
    generation INTEGER DEFAULT 0 NOT NULL,
    last_error TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    created_by TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    CONSTRAINT ew_commands_pkey PRIMARY KEY (namespace, command_id),
    CONSTRAINT ew_commands_namespace_event_id_key UNIQUE (namespace, event_id),
    CONSTRAINT ew_commands_namespace_event_id_fkey FOREIGN KEY(namespace, event_id) REFERENCES ew_inbox (namespace, event_id),
    CONSTRAINT ew_commands_state_check CHECK (state IN ('READY', 'RUNNING', 'DONE', 'FAILED', 'IGNORED'))
    )
    """)
    op.execute("""
    CREATE TABLE ew_outbox (
    namespace TEXT NOT NULL,
    command_id UUID NOT NULL,
    generation INTEGER DEFAULT 0 NOT NULL,
    state TEXT DEFAULT 'PENDING' NOT NULL,
    claim_token UUID,
    claim_until TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    created_by TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    CONSTRAINT ew_outbox_pkey PRIMARY KEY (namespace, command_id),
    CONSTRAINT ew_outbox_namespace_command_id_fkey FOREIGN KEY(namespace, command_id) REFERENCES ew_commands (namespace, command_id),
    CONSTRAINT ew_outbox_state_check CHECK (state IN ('PENDING', 'CLAIMED', 'SENT'))
    )
    """)
    op.execute("""
    CREATE TABLE ew_audit (
    id BIGINT GENERATED ALWAYS AS IDENTITY,
    namespace TEXT NOT NULL,
    command_id UUID NOT NULL,
    action TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    created_by TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    PRIMARY KEY (id)
    )
    """)
    op.execute("CREATE INDEX ew_commands_order ON ew_commands (namespace, execution_id, sequence) WHERE state NOT IN ('DONE', 'IGNORED')")
    op.execute('CREATE INDEX ew_outbox_pending ON ew_outbox (namespace, state, claim_until)')
