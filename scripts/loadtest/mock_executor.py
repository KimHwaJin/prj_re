"""HTTP submission stub: validates the production contract, never executes code."""
import asyncio
from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from uuid import NAMESPACE_URL, uuid5

from fastapi import FastAPI, HTTPException
from dtest.contracts.executor import ExecutorRequestBody, ExecutorSubmitResponse

app = FastAPI(title='Load-test Mock Executor')
DB_PATH = Path(os.getenv('MOCK_EXECUTOR_DB', '/app/var/mock-executor.sqlite'))
DELAY_MS = int(os.getenv('MOCK_EXECUTOR_DELAY_MS', '0'))
if not 0 <= DELAY_MS <= 60000:
    raise ValueError('MOCK_EXECUTOR_DELAY_MS must be between 0 and 60000')


@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(DB_PATH, timeout=30)) as db:
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS submissions (key TEXT PRIMARY KEY, digest TEXT NOT NULL, response TEXT NOT NULL)')
            yield db


@app.get('/health')
def health():
    with connect() as db:
        count = db.execute('SELECT count(*) FROM submissions').fetchone()[0]
    return {'mode': 'mock', 'unique_submissions': count, 'delay_ms': DELAY_MS}


def accept(body):
    payload = body.model_dump(mode='json', exclude_none=True)
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    key = body.idempotency_key
    execution = str(uuid5(NAMESPACE_URL, 'loadtest-executor:' + key))
    response = ExecutorSubmitResponse.model_validate({
        'execution_id': execution,
        'operation': {'operation_id': str(uuid5(NAMESPACE_URL, execution + ':operation')),
                      'steps': [{'sequence': s.sequence, 'step_id': str(uuid5(NAMESPACE_URL, execution + ':' + str(s.sequence)))}
                                for s in body.operation.spec.steps]},
        'state': {'status': 'QUEUED', 'version': 1},
    }).model_dump(mode='json', exclude_none=True)
    with connect() as db:
        db.execute('INSERT OR IGNORE INTO submissions VALUES (?, ?, ?)', (key, digest, json.dumps(response)))
        saved_digest, saved_response = db.execute('SELECT digest, response FROM submissions WHERE key=?', (key,)).fetchone()
        if saved_digest != digest:
            raise HTTPException(409, 'Idempotency key reused with a different payload')
    return json.loads(saved_response)


@app.post('/api/v1/executions', response_model=ExecutorSubmitResponse, status_code=202)
async def submit(body: ExecutorRequestBody):
    if DELAY_MS:
        await asyncio.sleep(DELAY_MS / 1000)
    return await asyncio.to_thread(accept, body)
