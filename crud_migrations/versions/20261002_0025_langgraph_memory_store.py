"""Move project memory into the official LangGraph PostgreSQL Store.

Pinned langgraph-checkpoint-postgres 3.1.2 schema (MIGRATIONS 0..3), without
concurrent index DDL inside Alembic's transaction. No vector or TTL policy.
0024 remains historical; its tables are removed after lossless data transfer.
Downgrade restores this application's documents only, keeping other Store data.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision = '20261002_0025'
down_revision = '20261002_0024'
branch_labels = None
depends_on = None

def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS store (
        prefix text NOT NULL, key text NOT NULL, value jsonb NOT NULL,
        created_at timestamptz DEFAULT CURRENT_TIMESTAMP, updated_at timestamptz DEFAULT CURRENT_TIMESTAMP,
        expires_at timestamptz, ttl_minutes int, PRIMARY KEY (prefix, key))""")
    op.execute('ALTER TABLE store ADD COLUMN IF NOT EXISTS expires_at timestamptz, ADD COLUMN IF NOT EXISTS ttl_minutes int')
    op.execute('CREATE INDEX IF NOT EXISTS store_prefix_idx ON store USING btree (prefix text_pattern_ops)')
    op.execute('CREATE INDEX IF NOT EXISTS idx_store_expires_at ON store (expires_at) WHERE expires_at IS NOT NULL')
    op.execute('CREATE TABLE IF NOT EXISTS store_migrations (v INTEGER PRIMARY KEY)')
    op.execute('INSERT INTO store_migrations(v) SELECT generate_series(0,3) ON CONFLICT DO NOTHING')
    op.execute("""INSERT INTO store(prefix,key,value,created_at,updated_at)
        SELECT 'dtest.project_memory.' || p.user_id::text || '.' || m.project_id::text || '.' || m.section,
        m.key, jsonb_build_object('section',m.section,'key',m.key,'content',m.content,'version',m.version,
            'is_deleted',m.is_deleted,'source',m.source,'updated_at',
            to_char(m.updated_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS') ||
            CASE WHEN extract(microseconds FROM m.updated_at)::bigint % 1000000 = 0 THEN ''
                 ELSE '.' || to_char(m.updated_at AT TIME ZONE 'UTC','US') END || '+00:00'), m.updated_at,m.updated_at
        FROM project_memories m JOIN projects p ON p.project_id=m.project_id""")
    op.execute("""INSERT INTO store(prefix,key,value,created_at,updated_at)
        SELECT 'dtest.project_memory_receipts.' || p.user_id::text || '.' || m.project_id::text,
        encode(sha256(convert_to(m.source_id,'UTF8')),'hex'),
        jsonb_build_object('source_id',m.source_id,'digest',m.digest,'result',m.result),m.created_at,m.created_at
        FROM project_memory_receipts m JOIN projects p ON p.project_id=m.project_id""")
    op.drop_table('project_memory_receipts')
    op.drop_table('project_memories')

def downgrade():
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

    op.execute("""INSERT INTO project_memories(project_id,section,key,content,version,is_deleted,source,updated_at)
        SELECT p.project_id,s.value->>'section',s.key,s.value->>'content',(s.value->>'version')::int,
        (s.value->>'is_deleted')::boolean,s.value->'source',(s.value->>'updated_at')::timestamptz
        FROM store s JOIN projects p ON p.project_id::text=split_part(s.prefix,'.',4)
        AND p.user_id::text=split_part(s.prefix,'.',3) WHERE starts_with(s.prefix,'dtest.project_memory.')""")
    op.execute("""INSERT INTO project_memory_receipts(project_id,source_id,digest,result,created_at)
        SELECT p.project_id,s.value->>'source_id',s.value->>'digest',s.value->'result',s.created_at
        FROM store s JOIN projects p ON p.project_id::text=split_part(s.prefix,'.',4)
        AND p.user_id::text=split_part(s.prefix,'.',3) WHERE starts_with(s.prefix,'dtest.project_memory_receipts.')""")
    op.execute("DELETE FROM store WHERE starts_with(prefix,'dtest.project_memory.') OR starts_with(prefix,'dtest.project_memory_receipts.')")
