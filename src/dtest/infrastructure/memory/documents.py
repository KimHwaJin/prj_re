"""Atomic official Store document/receipt writes under an application-owned lock."""
from datetime import datetime, timezone
from langgraph.store.base import GetOp, PutOp
from langgraph.store.postgres import AsyncPostgresStore
from psycopg_pool import AsyncConnectionPool
from dtest.contracts.memory_store import MEMORY_KEY, memory_namespace, receipt_namespace, receipt_key, memory_document
from dtest.contracts.project_memory import MemoryConflict, MemoryLimit, MAX_STORAGE_CHARS, replace_section

class DocumentWriter:
    def __init__(self, limits):
        self.limits=limits

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


async def write_document(store, limits, *args):
    if not isinstance(store, AsyncPostgresStore):
        raise TypeError('Durable project writes require the official AsyncPostgresStore')
    writer=DocumentWriter(limits)
    if isinstance(store.conn, AsyncConnectionPool):
        async with store.conn.connection() as conn:
            return await writer._transaction(conn, *args)
    return await writer._transaction(store.conn, *args)
