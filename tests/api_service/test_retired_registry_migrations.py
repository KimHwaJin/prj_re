"""Active SDK/event tables are preserved; retired storage is not hidden."""

from contextlib import nullcontext
from pathlib import Path
import runpy

from alembic import context
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Column, Integer, MetaData, Table, create_engine


def test_autogenerate_preserves_active_external_schema_owners(monkeypatch):
    # Execute the actual env module without migration side effects. Then apply its
    # filter to a real reflected schema and Alembic comparison, not a copied rule.
    captured = {}
    monkeypatch.setattr(context, "config", Config(), raising=False)
    monkeypatch.setattr(context, "is_offline_mode", lambda: True)
    monkeypatch.setattr(context, "configure", lambda **kw: captured.update(kw))
    monkeypatch.setattr(context, "begin_transaction", nullcontext)
    monkeypatch.setattr(context, "run_migrations", lambda: None)
    root = Path(__file__).resolve().parents[2]
    runpy.run_path(str(root / "crud_migrations/env.py"))
    assert "jupyter_servers" not in captured["target_metadata"].tables
    engine = create_engine("sqlite://")
    tables = MetaData()
    active = {
        "store",
        "store_migrations",
        "checkpoints",
        "checkpoint_blobs",
        "checkpoint_writes",
        "checkpoint_migrations",
        "ew_bindings",
        "ew_inbox",
        "ew_alembic_version",
    }
    for name in ("jupyter_servers", "unrelated_table", *sorted(active)):
        Table(name, tables, Column("id", Integer, primary_key=True))
    try:
        tables.create_all(engine)
        with engine.connect() as connection:
            migration = MigrationContext.configure(
                connection, opts={"include_object": captured["include_object"]}
            )
            changes = compare_metadata(migration, MetaData())
            assert sorted(
                (action, table.name) for action, table in changes
            ) == [
                ("remove_table", "jupyter_servers"),
                ("remove_table", "unrelated_table"),
            ]
            # If a future mapped counterpart exists, it remains comparable.
            assert captured["include_object"](
                None,
                "jupyter_servers",
                "table",
                True,
                tables.tables["jupyter_servers"],
            )
    finally:
        engine.dispose()
