"""Common graph command ledger; drain old dispatchers before upgrading."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261003_0026"
down_revision = "20261002_0025"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_commands",
        sa.Column("namespace", sa.Text(), primary_key=True),
        sa.Column(
            "command_id", postgresql.UUID(as_uuid=True), primary_key=True
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.session_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "ordinal",
            sa.BigInteger(),
            sa.Identity(),
            nullable=False,
            unique=True,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column(
            "invocation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.run_id", ondelete="CASCADE"),
        ),
        sa.Column("payload", postgresql.JSONB()),
        sa.Column("state", sa.Text(), nullable=False, server_default="READY"),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "failure_attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("owner_token", postgresql.UUID(as_uuid=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "kind IN ('user_start','user_resume','executor_resume')",
            name="agent_commands_kind",
        ),
        sa.CheckConstraint(
            (
                "state IN ('READY','RUNNING','DONE','IGNORED','FAILED','"
                "RECOVERY')"
            ),
            name="agent_commands_state",
        ),
        sa.CheckConstraint(
            (
                "(kind='executor_resume' AND invocation_id IS NULL AND "
                "payload IS NOT NULL) OR (kind IN "
                "('user_start','user_resume') AND invocation_id IS NOT "
                "NULL AND payload IS "
                "NULL)"
            ),
            name="agent_commands_input",
        ),
    )
    op.create_index(
        "agent_commands_ready",
        "agent_commands",
        ["namespace", "available_at", "ordinal"],
        postgresql_where=sa.text("state='READY'"),
    )
    op.create_index(
        "agent_commands_session_order",
        "agent_commands",
        ["session_id", "ordinal"],
        postgresql_where=sa.text("state NOT IN ('DONE','IGNORED','FAILED')"),
    )
    # Runtime namespace is explicit deployment configuration, not embedded in a
    # frozen migration. A backfill is performed separately by the migration tool.


def downgrade():
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM agent_commands WHERE "
            "state NOT IN ('DONE','IGNORED','FAILED'))"
        )
    ):
        raise RuntimeError(
            "Drain or recover all graph commands before downgrade"
        )
    op.drop_table("agent_commands")
