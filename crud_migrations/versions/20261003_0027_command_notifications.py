"""Commit-coupled command hints; ledger and SKIP LOCKED remain authoritative."""

from alembic import op

revision = "20261003_0027"
down_revision = "20261003_0026"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE FUNCTION notify_agent_command() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NEW.state IN ('READY','DONE','IGNORED','FAILED') THEN
        IF TG_OP = 'INSERT' THEN
          PERFORM pg_notify('dtest_agent_command_changed', md5(NEW.namespace));
        ELSIF NEW.state IS DISTINCT FROM OLD.state
              OR NEW.available_at IS DISTINCT FROM OLD.available_at THEN
          PERFORM pg_notify('dtest_agent_command_changed', md5(NEW.namespace));
        END IF;
      END IF;
      RETURN NULL;
    END $$;
    """)
    # Triggers cover API admission, psycopg Inbox routing, outcomes and backfill.
    # PostgreSQL delivers only after commit; rollback publishes nothing. Digest
    # bounds payload length and is only routing, never an authorization decision.
    op.execute("""CREATE TRIGGER agent_command_changed
        AFTER INSERT OR UPDATE OF state,available_at ON agent_commands
        FOR EACH ROW EXECUTE FUNCTION notify_agent_command()""")


def downgrade():
    op.execute("DROP TRIGGER agent_command_changed ON agent_commands")
    op.execute("DROP FUNCTION notify_agent_command()")
