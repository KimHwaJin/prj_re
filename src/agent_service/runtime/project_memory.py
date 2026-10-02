"""Bound current-quote-supported durable topics; never share session observations."""
import json
import re
from langchain_core.messages import HumanMessage
from service_contracts.project_memory import MemoryProposal

AUTO_SECTIONS={'background','analysis_preferences','report_preferences'}
SESSION_ONLY = re.compile(r'이번(?:만|에는|은|에|\s*(?:분석|보고서|결과))|지금만|방금|이번에만|this\s+(?:time|report|analysis|result)|just\s+for\s+now', re.I)
PERSISTENT = re.compile(r'앞으로|항상|프로젝트|기억|기본(?:으로|값)|계속|매번|always|remember|from\s+now|project|default', re.I)
PREFERENCE = re.compile(r'보고서는|분석에서는|선호|목표|목적|보고서.*(?:작성|독자)|prefer|reports?\s+should|goal|purpose', re.I)
FORBIDDEN = re.compile(r'\d|https?://|(?:^|\s)/|[a-zA-Z]:[\\/]|\.(?:parquet|csv|xlsx|json)(?:$|\W)')

def validate_memory_proposals(reply,request):
    updates=getattr(reply,'memory_updates',[])
    if not updates:return
    context=request.runtime.context
    if not getattr(context,'project_memory_auto_write',False) or getattr(context,'project_memory_policy',None) is None:
        raise ValueError('Automatic project memory writes are disabled; memory_updates must be empty')
    if reply.kind=='planning':raise ValueError('Planning selection must not write memory')
    limits=context.project_memory_limits
    if len(updates)>limits.max_updates:raise ValueError('Too many project memory updates')
    if 'project_memory_reference' in request.state and request.state['project_memory_reference'] is None:
        raise ValueError('Memory input disabled or budget too small; updates must be empty')
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
        if change.quote not in current or not change.quote.strip():
            raise ValueError('Memory provenance must be an exact non-blank CURRENT user quote')
        if len(change.content)>limits.topic_max_chars or len(change.quote)>limits.topic_max_chars:
            raise ValueError('Memory topic or provenance exceeds its configured limit')
        if SESSION_ONLY.search(change.quote) or (SESSION_ONLY.search(current) and not PERSISTENT.search(change.quote)):
            raise ValueError('Session-only requests must not update project memory')
        if not (PERSISTENT.search(change.quote) or PERSISTENT.search(current) or PREFERENCE.search(change.quote)):
            raise ValueError('No explicit durable project context or preference in the CURRENT quote')
        if FORBIDDEN.search(change.content) or FORBIDDEN.search(change.quote):
            raise ValueError('Automatic memory cannot share numeric statements, URLs or file paths; use explicit sharing')
        entry=entries.get(key)
        if change.expected_version!=(entry['version'] if entry else 0):raise ValueError('Memory proposal must echo the current topic version')
        if entry is not None and entry['is_deleted']:raise ValueError('Deleted topics require explicit user restoration, never automatic revival')
        if entry is not None and ' '.join(entry['content'].split())==' '.join(change.content.split()):raise ValueError('Do not rewrite unchanged memory topics')


def extract_memory_changes(schema,state):
    from agent_service.factory import json_output
    reply=json_output(schema)(state)
    return [update.model_dump() for update in reply.memory_updates]
