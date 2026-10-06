"""Owner-checked document CAS; model calls never hold a database connection."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import json
from uuid import UUID
from langgraph.store.base import GetOp, PutOp
from langgraph.store.postgres import AsyncPostgresStore
from psycopg_pool import AsyncConnectionPool
from sqlalchemy import select
from api_service.infrastructure.database import short_session
from api_service.models.enums import DeleteYN
from api_service.resources.lifecycle import lock_projects
from service_contracts.memory_store import MEMORY_KEY, memory_namespace, receipt_namespace, receipt_key, read_memory, memory_document
from service_contracts.project_memory import MemoryPatch, MemoryConflict, MemoryLimit, MemoryLimits, MAX_STORAGE_CHARS, MAX_UPDATE_SECTIONS, replace_section


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
            from api_service.infrastructure.memory_store import runtime
            async with runtime.open_store() as store:
                yield store

    async def read(self, user_id, project_id):
        user_id, project_id = UUID(str(user_id)), UUID(str(project_id))
        async with self.session() as db:
            await lock_projects(db, user_id, [project_id])
            async with self.storage() as store:
                return await read_memory(store, user_id, project_id)

    async def replace(self, user_id, project_id, content, expected_version, *, source_id):
        if not isinstance(content, str) or len(content) > MAX_STORAGE_CHARS:
            raise MemoryLimit('Project memory exceeds the absolute content bound')
        return await self._write(user_id, project_id, mode='replace', content=content,
                                 expected_version=expected_version, source_id=source_id, source={'kind': 'user_edit'})

    async def reset(self, user_id, project_id, expected_version, *, source_id):
        return await self._write(user_id, project_id, mode='reset', content='',
                                 expected_version=expected_version, source_id=source_id, source={'kind': 'user_edit'})

    async def apply(self, user_id, project_id, changes, *, source_id, source):
        patches = [MemoryPatch.model_validate(change) for change in changes]
        if not 1 <= len(patches) <= MAX_UPDATE_SECTIONS or len({c.section for c in patches}) != len(patches):
            raise MemoryLimit('A memory update must contain distinct bounded sections')
        if any(c.section == 'shared_findings' for c in patches):
            raise MemoryConflict('Analysis findings require an explicit document edit')
        version = patches[0].expected_version
        if any(c.expected_version != version for c in patches):
            raise MemoryConflict('All patches must use the same project document version')
        return await self._write(user_id, project_id, mode='patch', patches=patches,
                                 expected_version=version, source_id=source_id, source=source)

    async def _write(self, user_id, project_id, *, mode, expected_version, source_id, source, content=None, patches=()):
        user_id, project_id = UUID(str(user_id)), UUID(str(project_id))
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError('Invalid memory document version')
        if not isinstance(source_id, str) or not 1 <= len(source_id) <= 160:
            raise ValueError('Invalid memory source identity')
        body = {'mode': mode, 'content': content, 'expected_version': expected_version,
                'patches': [c.model_dump() for c in patches], 'source': source}
        digest = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        async with self.session() as db:
            # Serializes concurrent Pod writes and lifecycle changes in the
            # existing user/project lock order. Model inference is already over.
            await lock_projects(db, user_id, [project_id], exclusive=True)
            await self.require_execution_owner(db, source)
            async with self.storage() as store:
                if not isinstance(store, AsyncPostgresStore):
                    raise TypeError('Durable project writes require the official AsyncPostgresStore')
                if isinstance(store.conn, AsyncConnectionPool):
                    async with store.conn.connection() as conn:
                        return await self._transaction(conn, user_id, project_id, mode, content, patches, expected_version, source_id, source, digest)
                return await self._transaction(store.conn, user_id, project_id, mode, content, patches, expected_version, source_id, source, digest)

    @staticmethod
    async def require_execution_owner(db, source):
        from api_service.runs.claim_context import current_execution_claim
        claim = current_execution_claim.get()
        if source.get('kind') != 'user_request' or claim is None:
            return
        from api_service.models.agent_run_model import AgentRunModel
        from api_service.models.task_model import TaskModel
        from api_service.runs.tasks import TaskService
        row = (await db.execute(select(AgentRunModel, TaskModel).join(TaskModel, AgentRunModel.task_id == TaskModel.task_id)
            .where(AgentRunModel.run_id == claim.run_id).with_for_update(of=TaskModel))).one_or_none()
        if row is None:
            raise MemoryConflict('Memory source execution no longer exists')
        run, task = row
        TaskService.assert_execution_owner(run, task)
        if str(run.public_run_id) != source.get('run_id'):
            raise MemoryConflict('Memory source does not match the executing Run')

    async def _transaction(self, conn, user_id, project_id, mode, content, patches, expected_version, source_id, source, digest):
        namespace, receipts = memory_namespace(user_id, project_id), receipt_namespace(user_id, project_id)
        async with conn.transaction():
            store = AsyncPostgresStore(conn)
            receipt, item = await store.abatch([GetOp(receipts, receipt_key(source_id)), GetOp(namespace, MEMORY_KEY)])
            if receipt is not None:
                if receipt.value['digest'] != digest:
                    raise MemoryConflict('Memory source identity was reused with different content')
                return receipt.value['result']
            original = memory_document(item, user_id, project_id)
            if original['version'] != expected_version:
                raise MemoryConflict('Project memory changed; read the latest document before writing')
            if mode == 'patch':
                if len(patches) > self.limits.max_updates:
                    raise MemoryLimit('Memory update exceeds its configured section limit')
                content = original['content']
                for patch in patches:
                    if len(patch.content) > self.limits.patch_max_chars:
                        raise MemoryLimit('Memory replacement exceeds its configured limit')
                    content = replace_section(content, patch)
            chars, original_chars = len(content), len(original['content'])
            if chars > MAX_STORAGE_CHARS or (chars > self.limits.max_chars and chars >= original_chars):
                raise MemoryLimit('Project memory is full; explicitly shorten the document')
            # Reset/empty PUT are barriers even on an already-empty document.
            # Unchanged nonempty PUT can be replayed without a new version.
            changed = mode == 'reset' or not content or content != original['content']
            if changed:
                value = {'schema_version': 2, 'content': content, 'version': original['version'] + 1,
                         'source': source, 'updated_at': datetime.now(timezone.utc).isoformat()}
            else:
                value = original
            result = ({'status': 'saved', 'version': value['version']} if mode == 'patch' else
                      {**{k: value[k] for k in ('schema_version', 'content', 'version', 'updated_at')}, 'project_id': str(project_id)})
            ops = [PutOp(namespace, MEMORY_KEY, value)] if changed else []
            ops.append(PutOp(receipts, receipt_key(source_id), {'source_id': source_id, 'digest': digest, 'result': result}))
            # Document and receipt commit in one PostgreSQL transaction.
            await store.abatch(ops)
            return result

    def for_context(self, state):
        return BoundMemoryPolicy(self, state)


class BoundMemoryPolicy:
    """The Agent supplies section patches, never an owner or project key."""
    def __init__(self, service, state):
        self.service = service
        self.user_id, self.project_id, self.session_id = (str(state[k]) for k in ('user_id', 'project_id', 'session_id'))
        self.run_id = str(state.get('public_run_id') or state['run_id'])

    def policy(self, store):
        return ProjectMemoryPolicy(session_factory=self.service.session_factory, db=self.service.db,
                                   store=store, limits=self.service.limits)

    async def read(self, store):
        await self.require_source()
        return await self.policy(store).read(self.user_id, self.project_id)

    async def require_source(self):
        from api_service.models.session_model import SessionModel
        from api_service.models.agent_run_model import AgentRunModel
        async with self.service.session() as db:
            valid = await db.scalar(select(SessionModel.session_id).join(AgentRunModel, AgentRunModel.session_id == SessionModel.session_id).where(
                SessionModel.session_id == UUID(self.session_id), SessionModel.user_id == UUID(self.user_id),
                SessionModel.project_id == UUID(self.project_id), SessionModel.delete_yn == DeleteYN.N,
                AgentRunModel.run_id == UUID(self.run_id)))
            if valid is None:
                raise ValueError('Memory source Run does not belong to this user/project/session')

    async def apply(self, store, changes):
        await self.require_source()
        evidence = [{k: c[k] for k in ('section', 'quote', 'intent') if k in c} for c in changes]
        patches = [{k: v for k, v in c.items() if k not in ('quote', 'intent')} for c in changes]
        return await self.policy(store).apply(self.user_id, self.project_id, patches,
            source_id=self.run_id + ':conversation',
            source={'kind': 'user_request', 'run_id': self.run_id, 'session_id': self.session_id, 'changes': evidence})
