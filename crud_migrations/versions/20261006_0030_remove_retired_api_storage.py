"""Remove retired direct-LLM, project-sharing and Jupyter registry persistence.

Existing writers must be stopped before upgrade. Only named retired objects
are removed; active messages/runs/tasks/commands/Workflow/SDK Store stay intact.
Downgrade recreates empty legacy schema; discarded data cannot be recovered.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261006_0030"
down_revision = "20261005_0029"
branch_labels = None
depends_on = None

RETIRED_COLUMNS = ('agent_message_id', 'interpreted_message_id', 'workflow_stage', 'request_payload', 'redis_key', 'redis_result', 'dispatched_at', 'agent_completed_at', 'redis_received_at', 'interpreted_at')


def upgrade():
    for name in ("llm_runs", "project_members", "jupyter_servers"):
        op.drop_table(name)
    for name in RETIRED_COLUMNS:
        op.drop_column("agent_runs", name)
    op.execute("DROP TYPE llm_run_status")
    op.execute("DROP TYPE project_member_role")


def downgrade():
    # Schema rollback only: user requested deletion, not retention or archiving.
    op.execute("CREATE TYPE project_member_role AS ENUM ('owner', 'editor', 'viewer')")
    op.execute("CREATE TYPE llm_run_status AS ENUM ('queued', 'running', 'completed', 'failed', 'cancelled')")
    op.execute("""
    CREATE TABLE project_members (
    project_id UUID NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    member_role project_member_role NOT NULL DEFAULT 'owner', joined_dt TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, user_id))
    """)
    op.execute("""
    CREATE TABLE llm_runs (
    run_id UUID PRIMARY KEY, session_id UUID NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    trigger_message_id UUID NOT NULL REFERENCES messages(message_id) ON DELETE RESTRICT,
    assistant_message_id UUID REFERENCES messages(message_id) ON DELETE CASCADE,
    provider VARCHAR(50) NOT NULL, model_name VARCHAR(100) NOT NULL, temperature NUMERIC(4,3),
    max_output_tokens INTEGER, project_prompt_version INTEGER NOT NULL, system_prompt_snapshot TEXT NOT NULL,
    request_messages_snapshot JSONB NOT NULL, raw_response JSONB NOT NULL DEFAULT '{}'::jsonb,
    status llm_run_status NOT NULL DEFAULT 'queued', prompt_tokens INTEGER, completion_tokens INTEGER,
    total_tokens INTEGER, latency_ms INTEGER, finish_reason VARCHAR(100), error_code VARCHAR(100), error_message TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0, retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 5, attempt_errors JSONB NOT NULL DEFAULT '[]'::jsonb,
    queued_at TIMESTAMPTZ NOT NULL DEFAULT now(), started_at TIMESTAMPTZ, completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_llm_runs_trigger_message UNIQUE (trigger_message_id),
    CONSTRAINT uq_llm_runs_assistant_message UNIQUE (assistant_message_id))
    """)
    op.execute('CREATE INDEX ix_llm_runs_session_id ON llm_runs (session_id)')
    op.execute("""
    CREATE TABLE jupyter_servers (
    jupyter_server_id UUID PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    endpoint VARCHAR(1000) NOT NULL,
    token_ciphertext TEXT,
    health_status VARCHAR(20) NOT NULL DEFAULT 'unknown',
    last_http_status INTEGER,
    last_latency_ms INTEGER,
    last_error VARCHAR(500),
    last_checked_at TIMESTAMPTZ,
    created_by_user_id UUID REFERENCES users(user_id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at TIMESTAMPTZ,
    CONSTRAINT ck_jupyter_servers_health_status
    CHECK (health_status IN ('unknown', 'healthy', 'unhealthy'))
    )
    """)
    op.execute('CREATE INDEX ix_jupyter_servers_health_status ON jupyter_servers(health_status)')
    op.execute('CREATE INDEX ix_jupyter_servers_last_checked_at ON jupyter_servers(last_checked_at)')
    op.execute('CREATE INDEX ix_jupyter_servers_created_by_user_id ON jupyter_servers(created_by_user_id)')
    op.execute('CREATE UNIQUE INDEX uq_jupyter_servers_active_name ON jupyter_servers(name) WHERE deleted_at IS NULL')
    op.execute('CREATE UNIQUE INDEX uq_jupyter_servers_active_endpoint ON jupyter_servers(endpoint) WHERE deleted_at IS NULL')
    op.add_column("agent_runs", sa.Column('agent_message_id', postgresql.UUID(as_uuid=True)))
    op.add_column("agent_runs", sa.Column('interpreted_message_id', postgresql.UUID(as_uuid=True)))
    op.add_column("agent_runs", sa.Column('workflow_stage', sa.String(30), nullable=False, server_default="prepared"))
    op.add_column("agent_runs", sa.Column('request_payload', postgresql.JSONB()))
    op.add_column("agent_runs", sa.Column('redis_key', sa.String(500)))
    op.add_column("agent_runs", sa.Column('redis_result', postgresql.JSONB()))
    op.add_column("agent_runs", sa.Column('dispatched_at', sa.DateTime(timezone=True)))
    op.add_column("agent_runs", sa.Column('agent_completed_at', sa.DateTime(timezone=True)))
    op.add_column("agent_runs", sa.Column('redis_received_at', sa.DateTime(timezone=True)))
    op.add_column("agent_runs", sa.Column('interpreted_at', sa.DateTime(timezone=True)))
    op.create_foreign_key(None, "agent_runs", "messages", ['agent_message_id'], ["message_id"], ondelete="SET NULL")
    op.create_foreign_key(None, "agent_runs", "messages", ['interpreted_message_id'], ["message_id"], ondelete="SET NULL")
    op.create_index("ix_agent_runs_workflow_stage", "agent_runs", ['workflow_stage'])
    op.create_index("ix_agent_runs_redis_key", "agent_runs", ['redis_key'])
