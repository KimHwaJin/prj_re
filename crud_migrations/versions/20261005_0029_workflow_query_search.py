"""Native pgvector, multiple query embeddings and version-safe publication.

Install pgvector >=0.8.0 on PostgreSQL first. Old array vectors are preserved as
inactive history; their old composite text is not reinterpreted as user queries.
Run tools/provision_workflow_index.py after model settings are supplied.
"""

from alembic import op
import sqlalchemy as sa

revision = "20261005_0029"
down_revision = "20261004_0028"
branch_labels = depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    version = op.get_bind().scalar(
        sa.text("SELECT extversion FROM pg_extension WHERE extname='vector'")
    )
    if tuple(map(int, version.split(".")[:2])) < (0, 8):
        raise RuntimeError(
            "pgvector >=0.8.0 is required; upgrade the extension before migration"
        )
    op.add_column(
        "workflows",
        sa.Column(
            "user_queries",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default="[]",
        ),
    )
    for name in ("resource_revision", "search_revision"):
        op.add_column(
            "workflows",
            sa.Column(name, sa.Integer(), nullable=False, server_default="1"),
        )
    op.add_column(
        "workflows",
        sa.Column(
            "index_state", sa.String(20), nullable=False, server_default="not_indexed"
        ),
    )
    op.add_column("workflows", sa.Column("index_error", sa.String(100)))
    op.create_check_constraint(
        "ck_workflows_revisions",
        "workflows",
        "resource_revision > 0 AND search_revision > 0",
    )
    op.create_check_constraint(
        "ck_workflows_index_state",
        "workflows",
        "index_state IN ('not_indexed', 'pending', 'ready', 'failed')",
    )
    op.execute("UPDATE workflow_embeddings SET is_active=false, status='superseded'")
    op.drop_constraint(
        "ck_workflow_embeddings_ready_vector", "workflow_embeddings", type_="check"
    )
    op.execute(
        "ALTER TABLE workflow_embeddings ALTER COLUMN vector_values TYPE vector USING vector_values::vector"
    )
    op.add_column(
        "workflow_embeddings",
        sa.Column("search_revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column("workflow_embeddings", sa.Column("model_space", sa.String(64)))
    op.create_check_constraint(
        "ck_workflow_embeddings_ready_vector",
        "workflow_embeddings",
        "status <> 'ready' OR (vector_values IS NOT NULL AND vector_dims(vector_values)=dimensions)",
    )
    op.drop_constraint(
        "uq_workflow_embeddings_source_model", "workflow_embeddings", type_="unique"
    )
    op.drop_index("uq_workflow_embeddings_active_model", "workflow_embeddings")
    keys = ["workflow_id", "search_revision", "model_space", "embedded_text_sha256"]
    op.create_unique_constraint(
        "uq_workflow_embeddings_source_model", "workflow_embeddings", keys
    )


def downgrade():
    # Old code cannot represent several active queries: preserve their rows,
    # deactivate all and require explicit reindexing after a later upgrade.
    conn = op.get_bind()
    for (name,) in conn.execute(
        sa.text(
            "SELECT indexname FROM pg_indexes WHERE schemaname=current_schema() AND tablename='workflow_embeddings' AND indexname LIKE 'ix_workflow_hnsw_%'"
        )
    ):
        conn.execute(
            sa.text("DROP INDEX " + conn.dialect.identifier_preparer.quote(name))
        )
    op.execute("UPDATE workflow_embeddings SET is_active=false, status='superseded'")
    op.drop_constraint(
        "uq_workflow_embeddings_source_model", "workflow_embeddings", type_="unique"
    )
    # Historical identical text revisions collapse only the duplicate vector
    # rows when downgrading to the old unique key; refuse lossy downgrade.
    duplicates = conn.execute(
        sa.text(
            "SELECT 1 FROM workflow_embeddings GROUP BY workflow_id,model_provider,model_name,model_revision,embedded_text_sha256 HAVING count(*)>1 LIMIT 1"
        )
    ).first()
    if duplicates:
        raise RuntimeError(
            "Repeated query revisions exist; downgrade would lose embedding history"
        )
    op.drop_constraint(
        "ck_workflow_embeddings_ready_vector", "workflow_embeddings", type_="check"
    )
    op.execute(
        "ALTER TABLE workflow_embeddings ALTER COLUMN vector_values TYPE double precision[] USING vector_values::real[]"
    )
    op.create_check_constraint(
        "ck_workflow_embeddings_ready_vector",
        "workflow_embeddings",
        "status <> 'ready' OR (vector_values IS NOT NULL AND cardinality(vector_values)=dimensions)",
    )
    op.create_unique_constraint(
        "uq_workflow_embeddings_source_model",
        "workflow_embeddings",
        [
            "workflow_id",
            "model_provider",
            "model_name",
            "model_revision",
            "embedded_text_sha256",
        ],
    )
    op.create_index(
        "uq_workflow_embeddings_active_model",
        "workflow_embeddings",
        ["workflow_id", "model_provider", "model_name", "model_revision"],
        unique=True,
        postgresql_where=sa.text("is_active = true AND status = 'ready'"),
    )
    for name in ("model_space", "search_revision"):
        op.drop_column("workflow_embeddings", name)
    for name in ("ck_workflows_revisions", "ck_workflows_index_state"):
        op.drop_constraint(name, "workflows", type_="check")
    for name in (
        "user_queries",
        "resource_revision",
        "search_revision",
        "index_state",
        "index_error",
    ):
        op.drop_column("workflows", name)
