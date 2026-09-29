"""Offline A/B of token persistence scheduling, not LLM or database performance.

PYTHONPATH=src python scripts/benchmarks/token_event_buffer.py --output <report.json>
Loads the baseline buffer from Git without changing the checkout. Writers are
controlled coroutines; no HTTP, DB, Redis, LLM or Executor is contacted.
"""
import argparse
import asyncio
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

import service_settings
from api_service.services.llm_token_event_service import LLMTokenEventBuffer


def configure(**values):
    # Offline harness only: no application lifespan/resources exist between cases.
    service_settings._snapshot = None
    service_settings.configure(service_settings.load_settings(config={
        'AGENT_WORKER_ENABLED':False, 'EVENT_WORKER_ENABLED':False,
        'TASK_RECONCILER_ENABLED':False, **values,
    }, environ={}))


async def continuous(cls):
    configure(LLM_TOKEN_FLUSH_INTERVAL_SECONDS=.05, LLM_TOKEN_FLUSH_CHARACTERS=10000)
    buffer = cls(task_id=uuid4(), run_id=uuid4())
    written = []
    started = time.perf_counter()
    async def append(key, text):
        written.append((time.perf_counter()-started, text))
    buffer._append = append
    buffer.start()
    for _ in range(80):
        await buffer.on_llm_new_token('x', run_id='model')
        await asyncio.sleep(.005)
    production_seconds = time.perf_counter()-started
    await buffer.close()
    assert ''.join(text for _,text in written) == 'x'*80
    return {'scenario':'continuous_tokens','configured_interval_ms':50,
        'first_write_ms':written[0][0]*1000, 'producer_duration_ms':production_seconds*1000,
        'writes_before_producer_end':sum(at < production_seconds for at,_ in written),
        'total_writes':len(written),'content_exact':True}


async def stalled_writer(cls):
    configure(LLM_TOKEN_FLUSH_CHARACTERS=1, LLM_TOKEN_BUFFER_MAX_BYTES=64,
              LLM_TOKEN_BUFFER_MAX_ITEMS=8)
    buffer = cls(task_id=uuid4(), run_id=uuid4())
    entered, release = asyncio.Event(), asyncio.Event()
    accepted = 0
    stored = []
    async def append(key, text):
        entered.set()
        await release.wait()
        stored.append(text)
    buffer._append = append
    buffer.start()
    async def produce():
        nonlocal accepted
        for _ in range(1000):
            await buffer.on_llm_new_token('α', run_id='model')
            accepted += 1
    producer = asyncio.create_task(produce())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.sleep(.1)
        retained = accepted  # Writer is gated; none of the admitted tokens committed.
        was_blocked = not producer.done()
        release.set()
        await asyncio.wait_for(producer, 3)
        await buffer.close()
        assert ''.join(stored) == 'α'*1000
        return {'scenario':'stalled_writer','offered_tokens':1000,'gate_ms':100,
            'admitted_uncommitted_tokens':retained,'admitted_utf8_bytes':retained*2,
            'producer_waited_for_capacity':was_blocked,'content_exact':True}
    finally:
        release.set()
        producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)
        await buffer.close()


async def compare(before, repetitions):
    rows=[]
    for name,cls in [('before',before),('after',LLMTokenEventBuffer)]:
        for trial in range(repetitions):
            for measure in (continuous,stalled_writer):
                rows.append({'version':name,'trial':trial+1,**await measure(cls)})
    return rows


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline',default='039d516')
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[2]
    source=subprocess.check_output(['git','show',f'{args.baseline}:src/api_service/services/llm_token_event_service.py'],cwd=root)
    with tempfile.TemporaryDirectory(prefix='token-buffer-before-') as folder:
        path=Path(folder)/'baseline.py';path.write_bytes(source)
        spec=importlib.util.spec_from_file_location('baseline_token_buffer',path)
        module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
        rows=asyncio.run(compare(module.LLMTokenEventBuffer,3))
    result={'baseline':args.baseline,'method':'offline controlled writer, 3 trials each; no external services',
        'limits':['not LLM latency, real SQL throughput, RSS or production capacity',
                  'after buffer limits are set to 64 bytes / 8 items to expose backpressure'],
        'results':rows}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
