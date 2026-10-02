"""Application namespaces on the standard LangGraph Store, without a custom backend."""
from hashlib import sha256
from langgraph.store.base import BaseStore
from service_contracts.project_memory import MAX_STORAGE_TOPICS, MAX_STORAGE_CHARS, MemoryLimit

SECTIONS = {'background', 'analysis_preferences', 'report_preferences', 'shared_findings'}

def memory_namespace(user_id, project_id):
    return ('dtest', 'project_memory', str(user_id), str(project_id))

def receipt_namespace(user_id, project_id):
    return ('dtest', 'project_memory_receipts', str(user_id), str(project_id))

def receipt_key(source_id):
    return sha256(source_id.encode()).hexdigest()

def memory_entries(items, user_id, project_id):
    namespace = memory_namespace(user_id, project_id)
    entries = []
    for item in items:
        # A namespace labels data; it is not authorization. Validate returned
        # identity too, even when a custom Store is supplied by Agent developers.
        if len(item.namespace) != 5 or item.namespace[:4] != namespace or item.namespace[4] not in SECTIONS:
            raise ValueError('Store returned memory outside the requested namespace')
        row = dict(item.value)
        if row.get('section') != item.namespace[4] or row.get('key') != item.key:
            raise ValueError('Store memory identity does not match its value')
        entries.append(row)
    return entries

async def read_memory(store: BaseStore, user_id, project_id):
    items = await store.asearch(memory_namespace(user_id, project_id), limit=MAX_STORAGE_TOPICS + 1)
    if len(items) > MAX_STORAGE_TOPICS:
        raise MemoryLimit('Project memory exceeds the absolute scan bound')
    entries = memory_entries(items, user_id, project_id)
    entries.sort(key=lambda entry: (entry['section'], entry['key']))
    document = {'schema_version': 1, 'user_id': str(user_id), 'project_id': str(project_id), 'entries': entries}
    import json
    if len(json.dumps(document, ensure_ascii=False)) > MAX_STORAGE_CHARS:
        raise MemoryLimit('Project memory exceeds the absolute document bound')
    return document
