"""Validate extractive project topics against the current request and read version."""
import json
import re
from langchain_core.messages import HumanMessage
from service_contracts.project_memory import MemoryProposal

AUTO_SECTIONS={'background','analysis_preferences','report_preferences'}

def validate_memory_proposals(reply,request):
    updates=getattr(reply,'memory_updates',[])
    if not updates:return
    context=request.runtime.context
    if not getattr(context,'project_memory_auto_write',False) or getattr(context,'project_memory',None) is None:
        raise ValueError('Automatic project memory writes are disabled; memory_updates must be empty')
    if reply.kind=='planning':raise ValueError('Planning selection must not write memory')
    snapshot=request.state.get('project_memory_snapshot')
    if snapshot is None or snapshot['user_id']!=context.user_id or snapshot['project_id']!=context.project_id:
        raise ValueError('Memory requires an owner-checked snapshot')
    payload=json.loads(next(m.content for m in request.messages if isinstance(m,HumanMessage)))
    current=payload.get('request','')
    if not isinstance(current,str):raise ValueError('No current request for memory source')
    entries={(e['section'],e['key']):e for e in snapshot['entries']}
    keys=set()
    for raw in updates:
        change=MemoryProposal.model_validate(raw.model_dump() if hasattr(raw,'model_dump') else raw)
        key=(change.section,change.key)
        if key in keys:raise ValueError('Duplicate memory topic')
        keys.add(key)
        if change.section not in AUTO_SECTIONS:raise ValueError('Analysis findings require explicit project sharing, never automatic extraction')
        if change.content!=change.quote or change.quote not in current or not change.quote.strip():
            raise ValueError('Memory content must be an exact non-blank quote of the CURRENT user request')
        if re.search(r'\d|https?://|(?:^|\s)/|[a-zA-Z]:[\\/]|\.(?:parquet|csv|xlsx|json)(?:$|\W)',change.content):
            raise ValueError('Automatic memory cannot share numeric statements, URLs or file paths; use explicit sharing')
        entry=entries.get(key)
        if change.expected_version!=(entry['version'] if entry else 0):raise ValueError('Memory proposal must echo the current topic version')
        if entry is not None and entry['is_deleted']:raise ValueError('Deleted topics require explicit user restoration, never automatic revival')
        if entry is not None and entry['content']==change.content:raise ValueError('Do not rewrite unchanged memory topics')


def extract_memory_changes(schema,state):
    from agent_service.factory import json_output
    reply=json_output(schema)(state)
    return [update.model_dump(exclude={'quote'}) for update in reply.memory_updates]
