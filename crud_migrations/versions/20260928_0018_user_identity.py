"""Public user IDs and service roles, preserving all internal UUID references."""
from alembic import op
import sqlalchemy as sa

revision = "20260928_0018"
down_revision = "20260907_0017"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("public_user_id", sa.String(100), nullable=True))
    op.execute("UPDATE users SET public_user_id = user_id::text")
    op.alter_column("users", "public_user_id", nullable=False)
    op.create_unique_constraint("uq_users_public_user_id", "users", ["public_user_id"])
    op.create_check_constraint("ck_users_public_user_id", "users",
                               "public_user_id ~ '^[a-z0-9][a-z0-9_.@-]{0,99}$' AND public_user_id <> 'me'")
    op.add_column("users", sa.Column("role", sa.String(5), nullable=False, server_default="user"))
    op.create_check_constraint("user_role", "users", "role IN ('admin', 'user')")


def downgrade():
    op.drop_constraint("user_role", "users", type_="check")
    op.drop_column("users", "role")
    op.drop_constraint("ck_users_public_user_id", "users", type_="check")
    op.drop_constraint("uq_users_public_user_id", "users", type_="unique")
    op.drop_column("users", "public_user_id")
