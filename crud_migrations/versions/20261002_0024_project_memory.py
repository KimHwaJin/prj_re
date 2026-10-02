"""Create project topic memory and replay receipts; do not alter checkpoints."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision = '20261002_0024'
down_revision = '20260930_0023'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('project_memories',
        sa.Column('project_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('projects.project_id', ondelete='CASCADE'), primary_key=True),
        sa.Column('section', sa.String(32), primary_key=True), sa.Column('key', sa.String(48), primary_key=True),
        sa.Column('content', sa.Text(), nullable=False), sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('is_deleted', sa.Boolean(), nullable=False), sa.Column('source', postgresql.JSONB(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_table('project_memory_receipts',
        sa.Column('project_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('projects.project_id', ondelete='CASCADE'), primary_key=True),
        sa.Column('source_id', sa.String(160), primary_key=True), sa.Column('digest', sa.String(64), nullable=False),
        sa.Column('result', postgresql.JSONB(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))

def downgrade():
    op.drop_table('project_memory_receipts')
    op.drop_table('project_memories')
