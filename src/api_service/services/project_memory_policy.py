"""Owner-checked short transactions; no model call while holding a DB connection."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from langgraph.store.base import GetOp, SearchOp, PutOp
from langgraph.store.postgres import AsyncPostgresStore
from psycopg_pool import AsyncConnectionPool
from service_contracts.memory_store import memory_namespace, receipt_namespace, receipt_key, read_memory, memory_entries
import hashlib
import json
from uuid import UUID
from sqlalchemy import select
from api_service.core.database import short_session
from api_service.core.enums import DeleteYN
from api_service.services.resource_lifecycle import lock_projects
from service_contracts.project_memory import MemoryChange, MemoryConflict, MemoryLimit, MemoryLimits, MAX_STORAGE_TOPICS, MAX_STORAGE_CHARS

class ProjectMemoryPolicy:
    def __init__(self, *, session_factory=None, db=None, store=None, limits=None):
        self.session_factory = session_factory
        self.db = db
        self.store = store
        self._limits = limits

    @property
    def limits(self):
        if self._limits is not None:
            return self._limits
        from service_settings import get_settings
        return MemoryLimits.from_settings(get_settings().agent)

    @asynccontextmanager
    async def session(self):
        if self.db is not None:
            yield self.db
        else:
            async with short_session(self.session_factory) as db:
                yield db

    @asynccontextmanager
    async def storage(self):
        if self.store is not None:
            yield self.store
        else:
            from api_service.core.memory_store import runtime
            async with runtime.open_store() as store:
                yield store

    async def read(self, user_id, project_id):
        user_id, project_id = UUID(str(user_id)), UUID(str(project_id))
        async with self.session() as db:
            # Shared project barrier makes deletion and ownership changes linearizable.
            await lock_projects(db, user_id, [project_id])
            async with self.storage() as store:
                return await read_memory(store, user_id, project_id)

    async def apply(self, user_id, project_id, changes, *, source_id, source, delete=False, evidence=None):
        user_id, project_id = UUID(str(user_id)), UUID(str(project_id))
        changes = [MemoryChange.model_validate(change) for change in changes]
        from service_contracts.project_memory import MAX_UPDATE_TOPICS
        if not 1 <= len(changes) <= MAX_UPDATE_TOPICS or len({(c.section,c.key) for c in changes}) != len(changes):
            raise MemoryLimit('Memory batch exceeds its absolute limit or repeats a topic')
        if not isinstance(source_id,str) or not 1 <= len(source_id) <= 160: raise ValueError('Invalid memory source identity')
        body={'changes':[c.model_dump() for c in changes], 'source':source, 'delete':delete}
        if evidence:
            body['evidence'] = evidence
        digest=hashlib.sha256(json.dumps(body,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        async with self.session() as db:
            # Existing user/project lock order also serializes deletion and bounded writes.
            await lock_projects(db,user_id,[project_id],exclusive=True)
            # Reuse existing Worker ownership checks for this new durable side effect.
            # Claim-less explicit API writes do not acquire an execution slot.
            from api_service.core.execution_claim import current_execution_claim
            claim=current_execution_claim.get()
            if source.get('kind')=='user_request' and claim is not None:
                from api_service.models.common.agent_run_model import AgentRunModel
                from api_service.models.common.task_model import TaskModel
                from api_service.services.task_service import TaskService
                row=(await db.execute(select(AgentRunModel,TaskModel).join(TaskModel,AgentRunModel.task_id==TaskModel.task_id)
                    .where(AgentRunModel.run_id==claim.run_id).with_for_update(of=TaskModel))).one_or_none()
                if row is None: raise MemoryConflict('Memory source execution no longer exists')
                run,task=row
                TaskService.assert_execution_owner(run,task)
                if str(run.public_run_id)!=source.get('run_id'): raise MemoryConflict('Memory source does not match the executing Run')
            async with self.storage() as store:
                if not isinstance(store, AsyncPostgresStore):
                    raise TypeError('Durable project writes require the official AsyncPostgresStore')
                # Public constructor + abatch on an explicitly borrowed connection:
                # item writes and receipt commit in ONE PostgreSQL transaction.
                # Service user/project/Task locks above remain held until it commits.
                if isinstance(store.conn, AsyncConnectionPool):
                    async with store.conn.connection() as conn:
                        return await self.apply_transaction(conn, user_id, project_id, changes, source_id, source, digest, delete, evidence)
                return await self.apply_transaction(store.conn, user_id, project_id, changes, source_id, source, digest, delete, evidence)

    async def apply_transaction(self, conn, user_id, project_id, changes, source_id, source, digest, delete, evidence):
        namespace = memory_namespace(user_id, project_id)
        receipts = receipt_namespace(user_id, project_id)
        async with conn.transaction():
            transactional_store = AsyncPostgresStore(conn)
            receipt, items = await transactional_store.abatch([
                GetOp(receipts, receipt_key(source_id)), SearchOp(namespace, limit=MAX_STORAGE_TOPICS + 1)])
            if receipt is not None:
                if receipt.value['digest'] != digest:
                    raise MemoryConflict('Memory source identity was reused with different content')
                return receipt.value['result']
            # A previously committed identical request remains replayable when
            # deployment limits are lowered. New writes use today's limits.
            if len(changes)>self.limits.max_updates:
                raise MemoryLimit('Memory batch exceeds its configured limit')
            if not delete and any(len(c.content)>self.limits.topic_max_chars for c in changes):
                raise MemoryLimit('Memory topic exceeds its configured content limit')
            if len(items) > MAX_STORAGE_TOPICS:
                raise MemoryLimit('Project memory exceeds the absolute scan bound')
            entries = memory_entries(items, user_id, project_id)
            topics = {(row['section'], row['key']): row for row in entries}
            original_chars = len(json.dumps({'schema_version':1,'user_id':str(user_id),'project_id':str(project_id),'entries':list(topics.values())}, ensure_ascii=False))
            original_topics = len(topics)
            written, ops = [], []
            for change in changes:
                row = topics.get((change.section, change.key))
                if change.expected_version != (row['version'] if row else 0):
                    raise MemoryConflict('Memory topic changed; read its latest version before writing')
                if delete and row is None:
                    raise MemoryConflict('Cannot delete a missing topic')
                row = {'section': change.section, 'key': change.key, 'content': '' if delete else change.content,
                    'version': (row['version'] if row else 0) + 1, 'is_deleted': delete,
                    'source': {**source, **(evidence or {}).get(change.section + '/' + change.key, {})}, 'updated_at': datetime.now(timezone.utc).isoformat()}
                topics[(change.section, change.key)] = row
                ops.append(PutOp((*namespace, change.section), change.key, row))
                written.append({k: row[k] for k in ('section', 'key', 'version', 'is_deleted')})
            document = {'schema_version': 1, 'user_id': str(user_id), 'project_id': str(project_id), 'entries': list(topics.values())}
            chars = len(json.dumps(document, ensure_ascii=False))
            # Lowering deployment limits does not hide data or make gradual
            # correction impossible. Over-limit documents may only shrink.
            if len(topics)>MAX_STORAGE_TOPICS or chars>MAX_STORAGE_CHARS or (
                len(topics)>self.limits.max_topics and len(topics)>original_topics) or (
                chars>self.limits.max_chars and chars>=original_chars):
                raise MemoryLimit('Project memory is full; edit or shorten existing topics')
            result = {'status': 'saved', 'entries': written}
            ops.append(PutOp(receipts, receipt_key(source_id), {'source_id': source_id, 'digest': digest, 'result': result}))
            await transactional_store.abatch(ops)
            return result

    def for_context(self, state):
        return BoundMemoryPolicy(self,state)

class BoundMemoryPolicy:
    """The Agent supplies changes, never a different owner or project key."""
    def __init__(self, service, state):
        self.service=service
        self.user_id=str(state['user_id']);self.project_id=str(state['project_id']);self.session_id=str(state['session_id'])
        self.run_id=str(state.get('public_run_id') or state['run_id'])

    async def read(self, store):
        await self.require_source()
        policy = ProjectMemoryPolicy(session_factory=self.service.session_factory, db=self.service.db, store=store, limits=self.service.limits)
        return await policy.read(self.user_id,self.project_id)

    async def require_source(self):
        from api_service.models.common.session_model import SessionModel
        from api_service.models.common.agent_run_model import AgentRunModel
        async with self.service.session() as db:
            valid=await db.scalar(select(SessionModel.session_id).join(AgentRunModel,AgentRunModel.session_id==SessionModel.session_id).where(
                SessionModel.session_id==UUID(self.session_id),SessionModel.user_id==UUID(self.user_id),
                SessionModel.project_id==UUID(self.project_id),SessionModel.delete_yn==DeleteYN.N,
                AgentRunModel.run_id==UUID(self.run_id)))
            if valid is None: raise ValueError('Memory source Run does not belong to this user/project/session')

    async def apply(self, store, changes):
        await self.require_source()
        # Only user-request quotes are supplied; no session observations or file summaries.
        policy = ProjectMemoryPolicy(session_factory=self.service.session_factory, db=self.service.db, store=store, limits=self.service.limits)
        evidence = {c['section'] + '/' + c['key']: {k: c[k] for k in ('quote','intent') if k in c} for c in changes if 'quote' in c}
        changes = [{k:v for k,v in c.items() if k not in ('quote','intent')} for c in changes]
        return await policy.apply(self.user_id,self.project_id,changes, evidence=evidence,
            source_id=self.run_id+':conversation',
            source={'kind':'user_request','run_id':self.run_id,'session_id':self.session_id})
