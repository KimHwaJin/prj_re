from dtest.container import container
"""Real token commits, task row contention, public cursor replay and Run completion."""
import asyncio
from uuid import UUID, uuid4

import pytest
import dtest.settings.loader as service_settings
from langchain_core.callbacks.manager import AsyncCallbackManagerForLLMRun
from sqlalchemy import select

from dtest.application.runs import token_events as tokens
from dtest.contracts.enums import AgentRunStatus, TaskStatus
from dtest.application.runs.lifecycle import execution_health
from dtest.infrastructure.database.models.task_model import TaskModel
from dtest.infrastructure.database.models.task_event_model import TaskEventModel
from dtest.application.runs.task_events import TaskEventService
from dtest.contracts.execution import ExecutionNeedsRecovery
from tests.api_service.test_user_identity_postgres import database_url, harness
from tests.api_service.test_run_cleanup_postgres import runtime, enqueue, rows, worker, runs


@pytest.mark.asyncio
async def test_worker_hides_internal_model_tokens_and_replays_terminal_event(runtime, monkeypatch):
    observed = []
    model_ids = [uuid4(), uuid4()]
    async def graph(**kwargs):
        observed.extend(kwargs['callbacks'])
        for model_id, parts in zip(model_ids, [('가😀', ' tail'), ('다른', ' 모델')]):
            manager = AsyncCallbackManagerForLLMRun(run_id=model_id,
                handlers=container.callbacks(kwargs['callbacks']), inheritable_handlers=[])
            for part in parts:
                await manager.on_llm_new_token(part)
        return {'routing_result': {'route': 'analysis'}}
    monkeypatch.setattr(runs, 'ainvoke_user_turn', graph)
    queued = await enqueue(runtime)
    await worker.execute_claimed(await worker.claim_one())
    run, task = await rows(runtime, queued['run_id'])
    assert run.status == AgentRunStatus.SUCCESS and task.status == TaskStatus.SUCCESS
    async with runtime.factory() as db:
        events = await TaskEventService.list_after_public_run(db, run_id=UUID(queued['run_id']), sequence=0, limit=100)
        deltas = [event for event in events if event.event_type == 'llm.token.delta']
        assert not deltas and events[-1].event_type == 'task.success'
        replay = await TaskEventService.list_after_public_run(db, run_id=UUID(queued['run_id']),
            sequence=events[0].sequence, limit=100)
        assert [event.sequence for event in replay] == [event.sequence for event in events if event.sequence > events[0].sequence]
    assert observed[0].consumer.done() and observed[0].buffered_bytes == 0
    assert observed[0].peak_buffered_bytes == 0
    assert execution_health.healthy


@pytest.mark.asyncio
async def test_real_db_row_lock_bounds_buffer_then_drains_without_loss(runtime, monkeypatch):
    queued = await enqueue(runtime)
    run, task = await rows(runtime, queued['run_id'])
    monkeypatch.setattr(tokens, 'settings', service_settings.get_settings().api.model_copy(update={
        'llm_token_buffer_max_bytes':32, 'llm_token_buffer_max_items':2,
        'llm_token_flush_characters':1,
    }))
    b = tokens.LLMTokenEventBuffer(task_id=task.task_id, run_id=run.run_id)
    entered = asyncio.Event()
    original = b._append
    async def append(key, text):
        entered.set()
        await original(key, text)
    monkeypatch.setattr(b, '_append', append)
    b.start()
    producer = None
    try:
        async with runtime.factory() as held:
            await held.scalar(select(TaskModel).where(TaskModel.task_id == task.task_id).with_for_update())
            producer = asyncio.create_task(b.on_llm_new_token('가' * 20, run_id='model'))
            await asyncio.wait_for(entered.wait(), 1)
            await asyncio.sleep(.03)
            assert not producer.done() and not b.consumer.done()
            assert b.buffered_items == 2 and b.buffered_bytes <= 32
            await held.commit()
        await asyncio.wait_for(producer, 3)
        await b.close()
        async with runtime.factory() as db:
            events = await TaskEventService.list_after_public_run(db, run_id=run.public_run_id, sequence=0, limit=100)
        deltas = [event.payload for event in events if event.event_type == 'llm.token.delta']
        assert ''.join(event['delta'] for event in deltas) == '가' * 20
        assert [event['offset_start'] for event in deltas] == list(range(20))
        assert b.peak_buffered_items == 2 and b.peak_buffered_bytes <= 32
    finally:
        if producer:
            producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)
        await b.close()


@pytest.mark.asyncio
async def test_real_db_write_timeout_rolls_back_and_is_not_success(runtime, monkeypatch):
    queued = await enqueue(runtime)
    run, task = await rows(runtime, queued['run_id'])
    monkeypatch.setattr(tokens, 'settings', service_settings.get_settings().api.model_copy(update={
        'llm_token_write_timeout_seconds':.03, 'llm_token_flush_characters':1,
    }))
    b = tokens.LLMTokenEventBuffer(task_id=task.task_id, run_id=run.run_id)
    b.start()
    async with runtime.factory() as held:
        await held.scalar(select(TaskModel).where(TaskModel.task_id == task.task_id).with_for_update())
        await b.on_llm_new_token('x', run_id='model')
        done, _ = await asyncio.wait({b.consumer}, timeout=1)
        assert b.consumer in done
        await held.commit()
    with pytest.raises(ExecutionNeedsRecovery):
        await b.close()
    async with runtime.factory() as db:
        token_event = await db.scalar(select(TaskEventModel).where(
            TaskEventModel.run_id == run.run_id, TaskEventModel.event_type == 'llm.token.delta'))
        assert token_event is None
    assert b.offsets['model'] == 0 and b.buffered_bytes == 0
    assert not execution_health.healthy


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['write_error', 'admission_timeout'])
async def test_worker_internal_tokens_never_invoke_failed_or_blocked_public_writer(runtime, monkeypatch, failure):
    monkeypatch.setattr(tokens, 'settings', service_settings.get_settings().api.model_copy(update={
        'llm_token_buffer_max_items':1, 'llm_token_flush_characters':1,
        'llm_token_enqueue_timeout_seconds':.03,
    }))
    stopped = asyncio.Event()
    writes = []
    async def append(*args):
        writes.append(args)
        if failure == 'write_error':
            raise OSError('simulated writer failure')
        await asyncio.Event().wait()
    monkeypatch.setattr(tokens.LLMTokenEventBuffer, '_append', append)
    async def graph(**kwargs):
        manager = AsyncCallbackManagerForLLMRun(run_id=uuid4(),
            handlers=container.callbacks(kwargs['callbacks']), inheritable_handlers=[])
        try:
            await manager.on_llm_new_token('a')
            await manager.on_llm_new_token('b')
            return {'routing_result': {'route': 'analysis'}}
        finally:
            stopped.set()
    monkeypatch.setattr(runs, 'ainvoke_user_turn', graph)
    queued = await enqueue(runtime)
    await asyncio.wait_for(worker.execute_claimed(await worker.claim_one()), 2)
    assert stopped.is_set()
    assert writes == []
    run, task = await rows(runtime, queued['run_id'])
    assert not task.recovery_required and task.lock_token is None
    assert run.status == AgentRunStatus.SUCCESS and task.status == TaskStatus.SUCCESS
    async with runtime.factory() as db:
        events = await TaskEventService.list_after_public_run(db, run_id=run.public_run_id, sequence=0, limit=100)
    assert events[-1].event_type == 'task.success'
    assert not any(event.event_type in ('llm.token.delta','task.retry_scheduled') for event in events)
    assert execution_health.healthy
    assert await worker.claim_one() is None
