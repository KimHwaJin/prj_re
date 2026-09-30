"""One bounded completed analysis per session; not a project memory or file registry."""
from copy import deepcopy
import json


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def bounded_analysis(payload, max_chars):
    """Keep exact facts or omit them explicitly. Never truncate a metric's value."""
    if not max_chars:
        return None
    result = {k:payload[k] for k in ('source_run_id','execution_id','status')}
    goal = payload.get('goal','')
    result.update(goal=goal[:1000], goal_truncated=len(goal)>1000 or payload.get('goal_truncated',False),
        dataset_references=[], decisions={}, observations=[],
        omitted_dataset_references=payload.get('omitted_dataset_references',0),
        omitted_decisions=payload.get('omitted_decisions',0),
        omitted_observations=payload.get('omitted_observations',0),
        report={'status':payload.get('report',{}).get('status','not_requested'), 'excerpt':'', 'truncated':True},
        evidence_scope='bounded_executor_observations', text_only=True)

    def fits(candidate, reserve=0):
        return len(encoded(candidate)) <= max_chars-reserve

    # Reserve space for observations and report rather than let parameters fill all context.
    for row in payload.get('dataset_references',[]):
        candidate = {**result, 'dataset_references':[*result['dataset_references'],row]}
        if fits(candidate, max_chars//2):result = candidate
        else:result['omitted_dataset_references'] += 1
    for key, value in payload.get('decisions',{}).items():
        candidate = {**result, 'decisions':{**result['decisions'],key:value}}
        if fits(candidate, max_chars//2):result = candidate
        else:result['omitted_decisions'] += 1
    # Prefer the latest result; keep the original order of retained Steps.
    for row in reversed(payload.get('observations',[])):
        candidate = {**result, 'observations':[row,*result['observations']]}
        if not fits(candidate, max_chars//4):
            row = {**row, 'summary':None, 'summary_omitted':True}
            candidate = {**result, 'observations':[row,*result['observations']]}
        if fits(candidate, max_chars//4):result = candidate
        else:result['omitted_observations'] += 1
    report = payload.get('report',{})
    original = report.get('excerpt','')
    # JSON escaping can increase size; measure serialized text, not just Python string length.
    low, high = 0, len(original)
    while low < high:
        middle = (low+high+1)//2
        candidate = {**result, 'report':{**result['report'], 'excerpt':original[:middle],
            'truncated':report.get('truncated',False) or middle<len(original)}}
        if fits(candidate):low = middle
        else:high = middle-1
    result['report'].update(excerpt=original[:low], truncated=report.get('truncated',False) or low<len(original))
    assert fits(result), 'Session analysis metadata exceeds its configured budget'
    return deepcopy(result)


def analysis_for_owner(record, owner, max_chars):
    """A cached Agent must never borrow another session's completed result."""
    if not record or not max_chars or record.get('schema_version') != 1:
        return None
    expected = {key:str(owner.get(key) or '') for key in ('user_id','project_id','session_id')}
    if not all(expected.values()) or record.get('owner') != expected:
        return None
    payload = record.get('payload',{})
    if payload.get('status') not in {'analysis_completed','analysis_failed'}:
        return None
    return {'schema_version':1, 'owner':expected, 'payload':bounded_analysis(payload,max_chars)}


def capture_analysis(state, snapshot, final, max_chars):
    """Called after a real terminal event. Raw manifests, paths and Tool source stay out."""
    if not max_chars:
        return None
    payload = {'source_run_id':snapshot['run_id'], 'execution_id':final['execution_id'],
        'status':final['status'], 'goal':snapshot['document']['goal'],
        'dataset_references':[{'input_name':name,'dataset_id':item['dataset_id'],'title':item['title']}
            for name,item in snapshot.get('dataset_bindings',{}).items()],
        'decisions':state.get('execution_decisions',{}),
        'observations':final['observations'],
        'report':{'status':(final.get('report') or {}).get('status','not_requested'),
            'excerpt':(final.get('report') or {}).get('content','')}}
    return {'schema_version':1,
        'owner':{key:str(state[key]) for key in ('user_id','project_id','session_id')},
        'payload':bounded_analysis(payload,max_chars)}
