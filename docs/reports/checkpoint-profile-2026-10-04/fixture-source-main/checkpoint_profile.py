"""Opt-in test-process saver telemetry. No payloads and no second serialization.

Method duration includes serializer offload, pool/lock/DB wait and decode;
it is not PostgreSQL execution time. Concurrent calls overlap.
The SQL capture runs after the measured cohort drains, outside its wall time.
"""
from contextvars import ContextVar
from functools import wraps
import time

_call = ContextVar("checkpoint_profile_call", default=None)


def install(metrics, enabled):
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    for method in ("aget_tuple", "aput", "aput_writes"):
        original = getattr(AsyncPostgresSaver, method)

        def decorate(original, method):
            @wraps(original)
            async def measured(self, config, *args, **kwargs):
                if not enabled():
                    return await original(self, config, *args, **kwargs)
                identity = config["configurable"]
                row = {"method": method, "thread_id": identity["thread_id"],
                       "namespace": identity.get("checkpoint_ns", ""),
                       "checkpoint_id": identity.get("checkpoint_id"),
                       "start": time.perf_counter(), "error": None,
                       "serialization": []}
                if method == "aput":
                    checkpoint, metadata, versions = args
                    values = checkpoint["channel_values"]
                    row.update(checkpoint_id=checkpoint["id"], step=metadata.get("step"),
                               source=metadata.get("source"),
                               phase=values.get("execution_phase", "planning"),
                               changed_channels=list(versions))
                token = _call.set(row)
                try:
                    result = await original(self, config, *args, **kwargs)
                    if method == "aget_tuple" and result is not None:
                        row["checkpoint_id"] = result.checkpoint["id"]
                    return result
                except BaseException as exc:
                    row["error"] = type(exc).__name__
                    raise
                finally:
                    row["end"] = time.perf_counter()
                    metrics["checkpoint_calls"].append(row)
                    _call.reset(token)
            return measured
        setattr(AsyncPostgresSaver, method, decorate(original, method))

    for method in ("_dump_blobs", "_dump_writes"):
        original = getattr(AsyncPostgresSaver, method)

        def decorate_dump(original, method):
            @wraps(original)
            def measured(self, *args, **kwargs):
                began = time.perf_counter()
                result = original(self, *args, **kwargs)
                ended = time.perf_counter()
                row = _call.get()
                if row is not None:
                    # Measure the bytes already produced by the actual serializer.
                    row["serialization"].append({"method": method,
                        "ms": (ended-began)*1000,
                        "channels": [{"channel": item[2] if method == "_dump_blobs" else item[6],
                                      "bytes": len(item[-1]) if item[-1] else 0}
                                     for item in result]})
                return result
            return measured
        setattr(AsyncPostgresSaver, method, decorate_dump(original, method))


QUERIES = {
    "checkpoints": """
        SELECT thread_id, checkpoint_ns AS namespace, checkpoint_id, parent_checkpoint_id,
               metadata->>'step' AS step, metadata->>'source' AS source,
               checkpoint->'channel_values'->>'execution_phase' AS phase,
               octet_length(checkpoint::text) AS checkpoint_json_bytes,
               octet_length(metadata::text) AS metadata_json_bytes,
               pg_column_size(checkpoint) AS checkpoint_storage_bytes,
               pg_column_size(metadata) AS metadata_storage_bytes,
               checkpoint->'channel_versions' AS versions,
               (SELECT coalesce(jsonb_object_agg(key, octet_length(value::text)), '{}'::jsonb)
                FROM jsonb_each(checkpoint->'channel_values')) AS inline_bytes
        FROM checkpoints WHERE thread_id = ANY(%s)
        ORDER BY thread_id, checkpoint_ns, checkpoint_id
    """,
    "blobs": """
        SELECT thread_id, checkpoint_ns AS namespace, channel, version, type,
               coalesce(octet_length(blob), 0) AS bytes,
               coalesce(pg_column_size(blob), 0) AS storage_bytes
        FROM checkpoint_blobs WHERE thread_id = ANY(%s)
        ORDER BY thread_id, checkpoint_ns, channel, version
    """,
    "writes": """
        SELECT thread_id, checkpoint_ns AS namespace, checkpoint_id, task_id, idx,
               task_path, channel, type, octet_length(blob) AS bytes,
               pg_column_size(blob) AS storage_bytes
        FROM checkpoint_writes WHERE thread_id = ANY(%s)
        ORDER BY thread_id, checkpoint_ns, checkpoint_id, task_id, idx
    """,
}


def capture(dsn, thread_ids):
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(dsn, row_factory=dict_row) as db:
        result = {name: [dict(row) for row in db.execute(query, (thread_ids,))]
                  for name, query in QUERIES.items()}
        result["relations"] = [dict(row) for row in db.execute("""
            SELECT relname AS table, pg_relation_size(oid) AS heap_bytes,
                   pg_indexes_size(oid) AS indexes_bytes,
                   pg_total_relation_size(oid) AS total_bytes
            FROM pg_class WHERE relnamespace='public'::regnamespace
              AND relname IN ('checkpoints', 'checkpoint_blobs', 'checkpoint_writes')
            ORDER BY relname
        """)]
    # Relation sizes include warmup; selected payload rows exclude warmup threads.
    result["sql"] = QUERIES
    return result
