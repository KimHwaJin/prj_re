"""Commit-coupled wakeups for Run SSE; durable payloads remain in existing tables."""
from alembic import op

revision = '20260929_0022'
down_revision = '20260929_0021'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''
    CREATE FUNCTION notify_run_stream() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE rid uuid;
    BEGIN
      IF TG_TABLE_NAME = 'agent_runs' THEN
        IF TG_OP = 'DELETE' THEN rid := OLD.public_run_id; ELSE rid := NEW.public_run_id; END IF;
        PERFORM pg_notify('dtest_run_changed', rid::text);
      ELSIF TG_TABLE_NAME = 'task_events' THEN
        SELECT public_run_id INTO rid FROM agent_runs WHERE run_id = NEW.run_id;
        IF rid IS NOT NULL THEN PERFORM pg_notify('dtest_run_changed', rid::text); END IF;
      ELSIF TG_TABLE_NAME = 'tasks' THEN
        FOR rid IN SELECT DISTINCT public_run_id FROM agent_runs WHERE task_id = NEW.task_id LOOP
          PERFORM pg_notify('dtest_run_changed', rid::text);
        END LOOP;
      ELSE
        -- Rare access changes invalidate all local subscriptions; no payload/identity leaks.
        PERFORM pg_notify('dtest_run_changed', '*');
      END IF;
      RETURN NULL;
    END $$;
    ''')
    op.execute('''CREATE TRIGGER run_stream_change AFTER INSERT OR DELETE OR UPDATE OF
        status, interrupt, failure, agent_response, attempt_count, next_attempt_at,
        cancel_reason, cancel_requested_at, task_id, metadata, started_at, completed_at
        ON agent_runs FOR EACH ROW EXECUTE FUNCTION notify_run_stream()''')
    op.execute('''CREATE TRIGGER run_stream_event AFTER INSERT ON task_events
        FOR EACH ROW EXECUTE FUNCTION notify_run_stream()''')
    op.execute('''CREATE TRIGGER run_stream_task AFTER UPDATE OF
        status, recovery_required, cancel_requested_at, completed_at ON tasks
        FOR EACH ROW EXECUTE FUNCTION notify_run_stream()''')
    for table in ('users', 'projects', 'sessions'):
        columns = 'delete_yn' if table == 'users' else 'delete_yn, user_id'
        if table == 'sessions': columns += ', project_id'
        op.execute(f'''CREATE TRIGGER run_stream_access AFTER DELETE OR UPDATE OF {columns}
            ON {table} FOR EACH ROW EXECUTE FUNCTION notify_run_stream()''')


def downgrade():
    for table, trigger in [('agent_runs','run_stream_change'),('task_events','run_stream_event'),
                           ('tasks','run_stream_task'),('users','run_stream_access'),
                           ('projects','run_stream_access'),('sessions','run_stream_access')]:
        op.execute(f'DROP TRIGGER {trigger} ON {table}')
    op.execute('DROP FUNCTION notify_run_stream()')
