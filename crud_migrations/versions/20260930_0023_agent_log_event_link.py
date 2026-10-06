"""Link each Agent log to its durable event; preserve historical event sequences."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260930_0023"
down_revision = "20260929_0022"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "task_events",
        sa.Column(
            "agent_run_log_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
    )
    op.create_foreign_key(
        "fk_task_events_agent_run_log",
        "task_events",
        "agent_run_logs",
        ["agent_run_log_id"],
        ["log_id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_task_events_agent_run_log", "task_events", ["agent_run_log_id"]
    )
    # Older agent.event payloads have no event_key/log_id. Pair identical-content
    # occurrences deterministically; do not collapse repeated equal payloads.
    # This establishes a correspondence, not proof of historical timing/identity.
    # Missing events are repaired lazily by the normal log replay path.
    op.execute("""
        WITH log_payloads AS (
            SELECT log_id, run_id, created_at,
                   jsonb_build_object('agent_name', agent_name, 'node', node,
                       'event', event, 'kind', kind, 'payload', payload) AS body
            FROM agent_run_logs
        ), ranked_logs AS (
            SELECT *, row_number() OVER (
                PARTITION BY run_id, body ORDER BY created_at, log_id) AS occurrence
            FROM log_payloads
        ), ranked_events AS (
            SELECT task_event_id, run_id, payload,
                   row_number() OVER (
                       PARTITION BY run_id, payload ORDER BY sequence, task_event_id) AS occurrence
            FROM task_events WHERE event_type = 'agent.event'
        )
        UPDATE task_events AS event SET agent_run_log_id = log.log_id
        FROM ranked_events AS existing JOIN ranked_logs AS log
          ON log.run_id = existing.run_id AND log.body = existing.payload
         AND log.occurrence = existing.occurrence
        WHERE event.task_event_id = existing.task_event_id
    """)


def downgrade():
    op.drop_constraint(
        "uq_task_events_agent_run_log", "task_events", type_="unique"
    )
    op.drop_constraint(
        "fk_task_events_agent_run_log", "task_events", type_="foreignkey"
    )
    op.drop_column("task_events", "agent_run_log_id")
