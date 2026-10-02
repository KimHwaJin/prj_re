"""Per-invocation project knowledge; never a cache on the shared Agent object."""
import json
from typing_extensions import NotRequired
from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import HumanMessage
from service_contracts.project_memory import MemoryConflict, MemoryLimit
from agent_service.runtime.memory_selection import select_memory, dumps

class ProjectMemoryState(AgentState):
    project_memory_snapshot: NotRequired[dict | None]
    project_memory_write_result: NotRequired[dict | None]
    project_memory_reference: NotRequired[dict | None]

class ProjectMemoryMiddleware(AgentMiddleware):
    state_schema = ProjectMemoryState

    def __init__(self, *, extract_changes=None, role='analysis_conversation'):
        self.extract_changes=extract_changes
        self.role=role

    async def abefore_agent(self, state, runtime):
        context=runtime.context
        provider=getattr(context,'project_memory_policy',None)
        limits=context.project_memory_limits
        enabled=bool(limits.prompt_max_chars and limits.prompt_max_tokens)
        snapshot=await provider.read(runtime.store) if enabled and provider is not None and runtime.store is not None else None
        if snapshot is not None:
            if snapshot.get('user_id')!=context.user_id or snapshot.get('project_id')!=context.project_id:
                raise ValueError('Project memory owner does not match invocation')
        request=context.project_memory_request
        if not request:
            try:
                payload=json.loads(state['messages'][0].content)
                request=payload.get('request',payload.get('original_request',''))
            except (ValueError,AttributeError,KeyError,TypeError):
                request=''
        reference=select_memory(snapshot,role=self.role,request=request if isinstance(request,str) else '',
            limits=limits,automatic_write=bool(context.project_memory_auto_write and self.extract_changes)) if snapshot is not None else None
        return {'project_memory_snapshot':snapshot,'project_memory_reference':reference,'project_memory_write_result':None}

    async def awrap_model_call(self, request, handler):
        reference=request.state.get('project_memory_reference')
        if reference is None:
            return await handler(request)
        message=HumanMessage(id='dtest-project-memory',content=dumps(reference))
        messages=[m for m in request.messages if m.id!=message.id]
        index=next((i+1 for i,m in enumerate(messages) if isinstance(m,HumanMessage)),0)
        return await handler(request.override(messages=[*messages[:index],message,*messages[index:]]))

    async def aafter_agent(self, state, runtime):
        provider=getattr(runtime.context,'project_memory_policy',None)
        if provider is None or runtime.store is None or self.extract_changes is None:
            return None
        changes=self.extract_changes(state)
        if not changes:
            return None
        try:
            result=await provider.apply(runtime.store, changes)
        except (MemoryConflict,MemoryLimit) as exc:
            result={'status':'not_saved','reason':str(exc),'entries':[]}
        return {'project_memory_write_result':result}
