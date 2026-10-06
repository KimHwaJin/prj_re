"""Token producer/writer races, deadlines and LangChain callback backpressure."""
import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from pydantic import ValidationError
from langchain_core.callbacks.manager import AsyncCallbackManagerForLLMRun

import service_settings
from config import Settings
from api_service.runs.lifecycle import execution_health
from api_service.runs import token_events as tokens
from service_contracts.execution import ExecutionNeedsRecovery


@pytest_asyncio.fixture(autouse=True)
async def isolated(monkeypatch):
    monkeypatch.setattr(service_settings, '_snapshot', None)
    service_settings.configure(service_settings.load_settings(config={
        'AGENT_WORKER_ENABLED': False, 'TASK_RECONCILER_ENABLED': False,
        'EVENT_WORKER_ENABLED': False, 'RUN_CLEANUP_TIMEOUT_SECONDS': .2,
    }, environ={}))
    monkeypatch.setattr(execution_health, 'faults', {})
    monkeypatch.setattr(execution_health, 'recorders', set())
    monkeypatch.setattr(execution_health, '_record', AsyncMock())
    yield
    if execution_health.recorders:
        await asyncio.gather(*execution_health.recorders)


def buffer(monkeypatch, **options):
    cfg = service_settings.get_settings().api.model_copy(update=options)
    monkeypatch.setattr(tokens, 'settings', cfg)
    return tokens.LLMTokenEventBuffer(task_id=uuid4(), run_id=uuid4())


@pytest.mark.asyncio
async def test_continuous_tokens_flush_by_elapsed_time_before_producer_finishes(monkeypatch):
    b = buffer(monkeypatch, llm_token_flush_interval_seconds=.04, llm_token_flush_characters=10000)
    writes = []
    wrote = asyncio.Event()
    async def append(key, text):
        writes.append(text)
        wrote.set()
    monkeypatch.setattr(b, '_append', append)
    b.start()
    async def produce():
        for _ in range(40):
            await b.on_llm_new_token('x', run_id='llm')
            await asyncio.sleep(.005)  # Never idle for the configured flush interval.
    producer = asyncio.create_task(produce())
    try:
        await asyncio.wait_for(wrote.wait(), .15)
        assert not producer.done()
        await producer
        await b.close()
        assert ''.join(writes) == 'x' * 40
        assert len(writes) >= 3
    finally:
        producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)
        await b.close()


@pytest.mark.asyncio
async def test_slow_write_counts_inflight_payload_and_resumes_producer(monkeypatch):
    b = buffer(monkeypatch, llm_token_buffer_max_bytes=8, llm_token_buffer_max_items=2,
               llm_token_flush_characters=2)
    entered, release = asyncio.Event(), asyncio.Event()
    writes = []
    async def append(key, text):
        entered.set()
        await release.wait()
        writes.append(text)
    monkeypatch.setattr(b, '_append', append)
    b.start()
    await b.on_llm_new_token('😀😀', run_id='llm')
    await entered.wait()
    producer = asyncio.create_task(b.on_llm_new_token('끝', run_id='llm'))
    await asyncio.sleep(.02)
    assert not producer.done()
    assert b.buffered_bytes == 8  # Queue may be empty; writer still owns the payload.
    release.set()
    await asyncio.wait_for(producer, .5)
    await b.close()
    assert ''.join(writes) == '😀😀끝'
    assert b.peak_buffered_bytes <= 8 and b.peak_buffered_items <= 2
    assert b.buffered_bytes == b.buffered_items == 0


@pytest.mark.asyncio
async def test_oversized_unicode_callback_is_split_without_loss(monkeypatch):
    b = buffer(monkeypatch, llm_token_buffer_max_bytes=16, llm_token_buffer_max_items=2,
               llm_token_flush_characters=1000, llm_token_flush_interval_seconds=.005)
    writes = []
    async def append(key, text):
        assert len(text.encode()) <= 16
        writes.append(text)
    monkeypatch.setattr(b, '_append', append)
    b.start()
    original = '가😀éa' * 30
    await b.on_llm_new_token(original, run_id='llm')
    await b.close()
    assert ''.join(writes) == original
    assert b.peak_buffered_bytes <= 16 and b.peak_buffered_items <= 2


@pytest.mark.asyncio
async def test_item_limit_flushes_many_tiny_tokens_below_character_threshold(monkeypatch):
    b = buffer(monkeypatch, llm_token_buffer_max_items=2, llm_token_flush_characters=10000,
               llm_token_flush_interval_seconds=10)
    append = AsyncMock()
    monkeypatch.setattr(b, '_append', append)
    b.start()
    for _ in range(10):
        await asyncio.wait_for(b.on_llm_new_token('a', run_id='llm'), .2)
    await b.close()
    assert ''.join(call.args[1] for call in append.await_args_list) == 'a' * 10
    assert b.peak_buffered_items == 2


@pytest.mark.asyncio
async def test_langchain_admission_timeout_propagates_and_stops_writer(monkeypatch):
    b = buffer(monkeypatch, llm_token_buffer_max_items=1, llm_token_enqueue_timeout_seconds=.03)
    entered = asyncio.Event()
    async def append(*args):
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(b, '_append', append)
    b.start()
    manager = AsyncCallbackManagerForLLMRun(run_id=uuid4(), handlers=[b], inheritable_handlers=[])
    await manager.on_llm_new_token('first')
    await entered.wait()
    with pytest.raises(tokens.TokenEventBufferError, match='admission timed out'):
        await manager.on_llm_new_token('second')
    with pytest.raises(ExecutionNeedsRecovery):
        await b.close()
    assert b.consumer.done() and not execution_health.healthy
    assert b.buffered_bytes == b.buffered_items == 0


@pytest.mark.asyncio
async def test_writer_failure_wakes_all_blocked_callbacks(monkeypatch):
    b = buffer(monkeypatch, llm_token_buffer_max_items=1)
    entered, release = asyncio.Event(), asyncio.Event()
    async def append(*args):
        entered.set()
        await release.wait()
        raise OSError('simulated storage failure')
    monkeypatch.setattr(b, '_append', append)
    b.start()
    await b.on_llm_new_token('first', run_id='llm')
    await entered.wait()
    producers = [asyncio.create_task(b.on_llm_new_token('next', run_id='llm')) for _ in range(5)]
    await asyncio.sleep(.01)
    release.set()
    outcomes = await asyncio.wait_for(asyncio.gather(*producers, return_exceptions=True), .3)
    assert all(isinstance(error, tokens.TokenEventBufferError) for error in outcomes)
    with pytest.raises(ExecutionNeedsRecovery):
        await b.close()
    assert not b.queue and b.consumer.done()


@pytest.mark.asyncio
async def test_write_timeout_does_not_require_close_to_stop_writer(monkeypatch):
    b = buffer(monkeypatch, llm_token_write_timeout_seconds=.03, llm_token_flush_characters=1)
    async def append(*args):
        await asyncio.Event().wait()
    monkeypatch.setattr(b, '_append', append)
    b.start()
    await b.on_llm_new_token('x', run_id='llm')
    done, _ = await asyncio.wait({b.consumer}, timeout=.3)
    assert b.consumer in done
    with pytest.raises(ExecutionNeedsRecovery):
        await b.close()
    assert isinstance(b.failure, TimeoutError)


@pytest.mark.asyncio
async def test_cancel_waiting_producer_preserves_admitted_tail(monkeypatch):
    b = buffer(monkeypatch, llm_token_buffer_max_items=1)
    entered, release = asyncio.Event(), asyncio.Event()
    writes = []
    async def append(key, text):
        entered.set()
        await release.wait()
        writes.append(text)
    monkeypatch.setattr(b, '_append', append)
    b.start()
    await b.on_llm_new_token('first', run_id='llm')
    await entered.wait()
    producer = asyncio.create_task(b.on_llm_new_token('not admitted', run_id='llm'))
    await asyncio.sleep(.01)
    producer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await producer
    release.set()
    await b.close()
    assert writes == ['first'] and execution_health.healthy


@pytest.mark.asyncio
async def test_close_full_buffer_survives_repeated_owner_cancellation(monkeypatch):
    b = buffer(monkeypatch, llm_token_buffer_max_items=1)
    entered, release = asyncio.Event(), asyncio.Event()
    append = AsyncMock()
    async def write(key, text):
        entered.set()
        await release.wait()
        await append(key, text)
    monkeypatch.setattr(b, '_append', write)
    b.start()
    await b.on_llm_new_token('tail', run_id='llm')
    await entered.wait()
    closing = asyncio.create_task(b.close())
    await asyncio.sleep(.01)
    closing.cancel()
    await asyncio.sleep(.01)
    closing.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(closing, .5)
    await b.close()
    append.assert_awaited_once_with('llm', 'tail')
    assert b.consumer.done() and b.consumer.cancelling() == 0
    assert execution_health.healthy


@pytest.mark.asyncio
async def test_closed_buffer_rejects_late_tokens_and_restart(monkeypatch):
    b = buffer(monkeypatch)
    b.start()
    await b.close()
    with pytest.raises(tokens.TokenEventBufferError):
        await b.on_llm_new_token('late', run_id='llm')
    with pytest.raises(RuntimeError):
        b.start()


@pytest.mark.parametrize('values', [
    {'llm_token_buffer_max_bytes':3}, {'llm_token_buffer_max_items':0},
    {'llm_token_flush_characters':0}, {'llm_token_flush_interval_seconds':0},
    {'llm_token_flush_interval_seconds':float('nan')},
    {'llm_token_enqueue_timeout_seconds':-1}, {'llm_token_write_timeout_seconds':float('inf')},
])
def test_invalid_buffer_settings_fail_at_startup(values):
    with pytest.raises(ValidationError):
        Settings(**values)


def test_buffer_settings_follow_config_then_env_then_default():
    cfg = service_settings.load_settings(config={'LLM_TOKEN_BUFFER_MAX_BYTES':4096},
        environ={'LLM_TOKEN_BUFFER_MAX_BYTES':'8192','LLM_TOKEN_BUFFER_MAX_ITEMS':'32'})
    assert cfg.api.llm_token_buffer_max_bytes == 4096
    assert cfg.api.llm_token_buffer_max_items == 32
    assert cfg.api.llm_token_enqueue_timeout_seconds == 5


@pytest.mark.asyncio
async def test_writer_cancellation_waits_for_db_return_even_when_cancelled_again(monkeypatch):
    b = buffer(monkeypatch, llm_token_flush_characters=1)
    entered, closing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    class Session:
        async def close(self):
            closing.set()
            await release.wait()
    session = Session()
    monkeypatch.setattr(tokens, 'get_session_factory', lambda: lambda: session)
    async def append(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(tokens.TaskEventService, 'append', append)
    b.start()
    await b.on_llm_new_token('x', run_id='llm')
    await entered.wait()
    b.consumer.cancel()
    await closing.wait()
    b.consumer.cancel()
    await asyncio.sleep(.01)
    assert not b.consumer.done() and b.buffered_bytes == 1
    release.set()
    with pytest.raises(ExecutionNeedsRecovery):
        await b.close()
    assert b.consumer.done() and b.buffered_bytes == 0


@pytest.mark.asyncio
async def test_fragment_that_cannot_fit_remaining_bytes_triggers_flush(monkeypatch):
    b = buffer(monkeypatch, llm_token_buffer_max_bytes=8, llm_token_buffer_max_items=10,
               llm_token_flush_characters=1000, llm_token_flush_interval_seconds=10)
    writes = []
    async def append(key, text):
        writes.append(text)
    monkeypatch.setattr(b, '_append', append)
    b.start()
    # Seven of eight bytes: the next two-byte fragment cannot fit, even though
    # neither configured limit is exactly reached. It must wake the partial batch.
    await b.on_llm_new_token('가', run_id='llm')
    await b.on_llm_new_token('😀', run_id='llm')
    await asyncio.wait_for(b.on_llm_new_token('ab', run_id='llm'), .2)
    await b.close()
    assert ''.join(writes) == '가😀ab'
    assert b.peak_buffered_bytes <= 8
