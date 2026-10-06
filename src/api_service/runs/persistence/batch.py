"""Transaction-local message context for one default graph result projection.

Never cache this across graph steps, commits, DB sessions or LLM waits. Acquire
the Run log barrier, then user/project/session barriers before any log event
locks the Task row. Never wait for the log barrier holding a Session row lock. The dispatcher owns commit/rollback.
"""
from uuid import UUID

from api_service.resources import lifecycle as resource_lifecycle
from api_service.resources.messages import MessageService
from api_service.runs.logs import AgentRunLogService


class GraphResultBatch:
    def __init__(self, db, user_id, session, log_run_id):
        self.db = db
        self.user_id = user_id
        self.session = session
        self.log_run_id = log_run_id
        self.transaction = db.get_transaction()

    @classmethod
    async def prepare(cls, db, state, context, events):
        log_run_id = None
        if context.agent_run_id and any(event.target in {"agent_runs", "plans", "workflow_logs"} for event in events):
            log_run_id = UUID(context.agent_run_id)
            await AgentRunLogService.lock_run(db, log_run_id)
        session = None
        if any(event.target == "messages" for event in events):
            project_id = state.get("project_id")
            session = await resource_lifecycle.lock_session(
                db, context.user_id, UUID(context.session_id),
                expected_project_id=UUID(str(project_id)) if project_id else None,
                for_update=True,
            )
        return cls(db, context.user_id, session, log_run_id)

    def require_log_context(self, db, run_id):
        if (db is not self.db or db.get_transaction() is not self.transaction
                or run_id != self.log_run_id):
            raise RuntimeError("Graph result batch cannot reuse a different log transaction")

    async def create_message(self, db, user_id, payload):
        if (db is not self.db or db.get_transaction() is not self.transaction
                or self.session is None or user_id != self.user_id
                or payload.session_id != self.session.session_id
                or (payload.project_id is not None and payload.project_id != self.session.project_id)):
            raise RuntimeError("Graph result batch cannot reuse a different session or transaction")
        return await MessageService._create_locked(db, self.session, payload, commit=False)
