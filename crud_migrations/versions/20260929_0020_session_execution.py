"""One persistent graph owner across API Runs and Executor events."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260929_0020"
down_revision = "20260928_0019"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "session_executions",
        sa.Column("session_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("token", postgresql.UUID(as_uuid=True)),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True)),
        sa.Column("owner_kind", sa.String(20)),
        sa.Column("owner_process", sa.String(255)),
        sa.Column("acquired_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("recovery_required", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("recovery_reason", sa.Text()),
    )


def downgrade():
    op.drop_table("session_executions")
