"""A retired ORM mapping must not turn normal autogenerate into data deletion."""
from contextlib import nullcontext
from pathlib import Path
import runpy

from alembic import context
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Column, Integer, MetaData, Table, create_engine


def test_autogenerate_preserves_retired_registry_and_sdk_store(monkeypatch):
    # Execute the actual env module without migration side effects. Then apply its
    # filter to a real reflected schema and Alembic comparison, not a copied rule.
    captured = {}
    monkeypatch.setattr(context, 'config', Config(), raising=False)
    monkeypatch.setattr(context, 'is_offline_mode', lambda: True)
    monkeypatch.setattr(context, 'configure', lambda **kw: captured.update(kw))
    monkeypatch.setattr(context, 'begin_transaction', nullcontext)
    monkeypatch.setattr(context, 'run_migrations', lambda: None)
    root = Path(__file__).resolve().parents[3]
    runpy.run_path(str(root / 'crud_migrations/env.py'))
    assert 'jupyter_servers' not in captured['target_metadata'].tables
    engine = create_engine('sqlite://')
    tables = MetaData()
    for name in ('jupyter_servers', 'store', 'store_migrations', 'unrelated_table'):
        Table(name, tables, Column('id', Integer, primary_key=True))
    try:
        tables.create_all(engine)
        with engine.connect() as connection:
            migration = MigrationContext.configure(connection,
                opts={'include_object': captured['include_object']})
            changes = compare_metadata(migration, MetaData())
            assert [(action, table.name) for action, table in changes] == [('remove_table', 'unrelated_table')]
            # If a future mapped counterpart exists, it remains comparable.
            assert captured['include_object'](None, 'jupyter_servers', 'table', True, tables.tables['jupyter_servers'])
    finally:
        engine.dispose()
