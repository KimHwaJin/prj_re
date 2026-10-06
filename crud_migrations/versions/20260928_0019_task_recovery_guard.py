"""Keep uncertain Run ownership out of automatic retry."""

from alembic import op
import sqlalchemy as sa

revision = "20260928_0019"
down_revision = "20260928_0018"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "tasks",
        sa.Column(
            "recovery_required",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade():
    op.drop_column("tasks", "recovery_required")
