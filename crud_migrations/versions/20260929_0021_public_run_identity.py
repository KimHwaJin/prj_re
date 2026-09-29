"""Stable public Run identity; retain invocation, Task and checkpoint IDs.

Drain API/event workers before upgrading. Old writers cannot populate the new
required column. No checkpoint data or Executor binding is rewritten.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260929_0021"
down_revision = "20260929_0020"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("agent_runs", sa.Column("public_run_id", postgresql.UUID(as_uuid=True)))
    # Prefer the persisted Task root, then a valid same-session checkpoint root.
    # Compare UUIDs as strings to avoid casts failing on malformed old metadata.
    op.execute("""
        UPDATE agent_runs r SET public_run_id = COALESCE(
          (SELECT origin.run_id FROM tasks t JOIN agent_runs origin ON origin.run_id=t.root_run_id
           WHERE t.task_id=r.task_id AND origin.session_id=r.session_id),
          (SELECT origin.run_id FROM agent_runs origin
           WHERE origin.run_id::text=r.metadata->>'checkpoint_run_id' AND origin.session_id=r.session_id),
          r.run_id)
    """)
    # Reject ambiguous/cyclic historical chains rather than merge unrelated jobs.
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM agent_runs r JOIN agent_runs p ON p.run_id=r.public_run_id
                     WHERE p.public_run_id<>p.run_id) THEN
            RAISE EXCEPTION 'Non-root historical Run reference: resolve before public Run migration';
          END IF;
          IF EXISTS (SELECT public_run_id FROM agent_runs GROUP BY public_run_id
                     HAVING count(DISTINCT task_id)>1) THEN
            RAISE EXCEPTION 'Multiple Tasks for one public Run: reconcile event sequences before migration';
          END IF;
        END $$
    """)
    op.alter_column("agent_runs", "public_run_id", nullable=False)
    op.create_foreign_key("fk_agent_runs_public_run", "agent_runs", "agent_runs",
                          ["public_run_id"], ["run_id"], ondelete="CASCADE",
                          deferrable=True, initially="DEFERRED")
    op.create_index("ix_agent_runs_public_latest", "agent_runs", ["public_run_id", "created_at", "run_id"])


def downgrade():
    op.drop_index("ix_agent_runs_public_latest", table_name="agent_runs")
    op.drop_constraint("fk_agent_runs_public_run", "agent_runs", type_="foreignkey")
    op.drop_column("agent_runs", "public_run_id")
