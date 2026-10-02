"""Owner-checked short transactions; no model call while holding a DB connection."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import json
from uuid import UUID
from sqlalchemy import select
from fastapi import HTTPException
from api_service.core.database import short_session
from api_service.core.enums import DeleteYN
from api_service.models.common.project_model import ProjectModel
from api_service.models.common.user_model import UserModel
from api_service.models.common.project_memory_model import ProjectMemoryModel, ProjectMemoryReceiptModel
from api_service.services.resource_lifecycle import lock_projects
from service_contracts.project_memory import MemoryChange, MemoryConflict, MemoryLimit

MAX_TOPICS = 64
MAX_MEMORY_CHARS = 16000

def public_item(row):
    return {'section':row.section, 'key':row.key, 'content':row.content, 'version':row.version,
            'is_deleted':row.is_deleted, 'source':row.source, 'updated_at':row.updated_at.isoformat()}

def document(user_id, project_id, rows):
    return {'schema_version':1, 'user_id':str(user_id), 'project_id':str(project_id),
            'entries':[public_item(row) for row in rows]}

class ProjectMemoryService:
    def __init__(self, *, session_factory=None, db=None):
        self.session_factory = session_factory
        self.db = db

    @asynccontextmanager
    async def session(self):
        if self.db is not None:
            yield self.db
        else:
            async with short_session(self.session_factory) as db:
                yield db

    async def read(self, user_id, project_id):
        user_id, project_id = UUID(str(user_id)), UUID(str(project_id))
        async with self.session() as db:
            exists = await db.scalar(select(ProjectModel.project_id).join(UserModel, UserModel.user_id==ProjectModel.user_id).where(
                ProjectModel.project_id==project_id, ProjectModel.user_id==user_id, ProjectModel.delete_yn==DeleteYN.N,
                UserModel.delete_yn==DeleteYN.N))
            if exists is None: raise HTTPException(404, 'Project not found.')
            rows = list(await db.scalars(select(ProjectMemoryModel).where(ProjectMemoryModel.project_id==project_id)
                                        .order_by(ProjectMemoryModel.section, ProjectMemoryModel.key)))
            return document(user_id, project_id, rows)

    async def apply(self, user_id, project_id, changes, *, source_id, source, delete=False):
        user_id, project_id = UUID(str(user_id)), UUID(str(project_id))
        changes = [MemoryChange.model_validate(change) for change in changes]
        if not 1 <= len(changes) <= 4 or len({(c.section,c.key) for c in changes}) != len(changes):
            raise ValueError('Memory writes require 1..4 distinct topics')
        if not isinstance(source_id,str) or not 1 <= len(source_id) <= 160: raise ValueError('Invalid memory source identity')
        body={'changes':[c.model_dump() for c in changes], 'source':source, 'delete':delete}
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
            receipt=await db.get(ProjectMemoryReceiptModel,(project_id,source_id))
            if receipt is not None:
                if receipt.digest != digest: raise MemoryConflict('Memory source identity was reused with different content')
                return receipt.result
            rows=list(await db.scalars(select(ProjectMemoryModel).where(ProjectMemoryModel.project_id==project_id)))
            topics={(row.section,row.key):row for row in rows}
            written=[]
            for change in changes:
                row=topics.get((change.section,change.key))
                if change.expected_version != (row.version if row else 0): raise MemoryConflict('Memory topic changed; read its latest version before writing')
                if delete and row is None: raise MemoryConflict('Cannot delete a missing topic')
                if row is None:
                    row=ProjectMemoryModel(project_id=project_id,section=change.section,key=change.key,version=0)
                    db.add(row);rows.append(row);topics[(change.section,change.key)]=row
                row.content='' if delete else change.content
                row.version+=1;row.is_deleted=delete;row.source=source;row.updated_at=datetime.now(timezone.utc)
                written.append({'section':row.section,'key':row.key,'version':row.version,'is_deleted':delete})
            # Tombstones retain versions to prevent stale create/delete/recreate overwrites.
            if len(rows)>MAX_TOPICS or len(json.dumps(document(user_id,project_id,rows),ensure_ascii=False))>MAX_MEMORY_CHARS:
                raise MemoryLimit('Project memory is full; edit or shorten existing topics')
            result={'status':'saved','entries':written}
            db.add(ProjectMemoryReceiptModel(project_id=project_id,source_id=source_id,digest=digest,result=result))
            await db.commit()
            return result

    def for_context(self, state):
        return BoundProjectMemory(self,state)

class BoundProjectMemory:
    """The Agent supplies changes, never a different owner or project key."""
    def __init__(self, service, state):
        self.service=service
        self.user_id=str(state['user_id']);self.project_id=str(state['project_id']);self.session_id=str(state['session_id'])
        self.run_id=str(state.get('public_run_id') or state['run_id'])

    async def read(self, project_id):
        if str(project_id)!=self.project_id: raise ValueError('Memory project does not match invocation')
        await self.require_source()
        return await self.service.read(self.user_id,self.project_id)

    async def require_source(self):
        from api_service.models.common.session_model import SessionModel
        from api_service.models.common.agent_run_model import AgentRunModel
        async with self.service.session() as db:
            valid=await db.scalar(select(SessionModel.session_id).join(AgentRunModel,AgentRunModel.session_id==SessionModel.session_id).where(
                SessionModel.session_id==UUID(self.session_id),SessionModel.user_id==UUID(self.user_id),
                SessionModel.project_id==UUID(self.project_id),SessionModel.delete_yn==DeleteYN.N,
                AgentRunModel.run_id==UUID(self.run_id)))
            if valid is None: raise ValueError('Memory source Run does not belong to this user/project/session')

    async def apply(self, changes):
        await self.require_source()
        # Only user-request quotes are supplied; no session observations or file summaries.
        return await self.service.apply(self.user_id,self.project_id,changes,
            source_id=self.run_id+':conversation',
            source={'kind':'user_request','run_id':self.run_id,'session_id':self.session_id})
