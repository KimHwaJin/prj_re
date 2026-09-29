"""Real token commits, task row contention, public cursor replay and Run completion."""
import asyncio
from uuid import UUID, uuid4

import pytest
import service_settings
from langchain_core.callbacks.manager import AsyncCallbackManagerForLLMRun
from sqlalchemy import select

from api_service.services import llm_token_event_service as tokens
from api_service.core.enums import AgentRunStatus, TaskStatus
from api_service.core.execution_lifecycle import execution_health
from api_service.models.common.task_model import TaskModel
from api_service.models.common.task_event_model import TaskEventModel
from api_service.services.task_event_service import TaskEventService
from service_contracts.execution import ExecutionNeedsRecovery
from api_service.test.test_user_identity_postgres import database_url, harness
from api_service.test.test_run_cleanup_postgres import runtime, enqueue, rows, worker, runs


@pytest.mark.asyncio
async def test_worker_commits_unicode_tokens_before_terminal_event_and_cursor_replays(runtime, monkeypatch):
    observed = []
    model_ids = [uuid4(), uuid4()]
    async def graph(**kwargs):
        observed.extend(kwargs['callbacks'])
        for model_id, parts in zip(model_ids, [('가😀', ' tail'), ('다른', ' 모델')]):
            manager = AsyncCallbackManagerForLLMRun(run_id=model_id,
                handlers=kwargs['callbacks'], inheritable_handlers=[])
            for part in parts:
                await manager.on_llm_new_token(part)
        return {'routing_result': {'route': 'analysis'}}
    monkeypatch.setattr(runs, 'ainvoke_user_turn', graph)
    queued = await enqueue(runtime)
    await worker.execute_claimed(await worker.claim_one())
    run, task = await rows(runtime, queued['id'])
    assert run.status == AgentRunStatus.SUCCESS and task.status == TaskStatus.SUCCESS
    async with runtime.factory() as db:
        events = await TaskEventService.list_after_public_run(db, run_id=UUID(queued['id']), sequence=0, limit=100)
        deltas = [event for event in events if event.event_type == 'llm.token.delta']
        assert deltas and events[-1].event_type == 'task.success'
        assert max(event.sequence for event in deltas) < events[-1].sequence
        for model_id, expected in zip(model_ids, ['가😀 tail', '다른 모델']):
            selected = [event for event in deltas if event.payload['llm_run_id'] == str(model_id)]
            assert ''.join(event.payload['delta'] for event in selected) == expected
            offset = 0
            for event in selected:
                assert event.payload['offset_start'] == offset
                offset += len(event.payload['delta'])
                assert event.payload['offset_end'] == offset
        replay = await TaskEventService.list_after_public_run(db, run_id=UUID(queued['id']),
            sequence=deltas[0].sequence, limit=100)
        assert [event.sequence for event in replay] == [event.sequence for event in events if event.sequence > deltas[0].sequence]
    assert observed[0].consumer.done() and observed[0].buffered_bytes == 0
    assert execution_health.healthy


@pytest.mark.asyncio
async def test_real_db_row_lock_bounds_buffer_then_drains_without_loss(runtime, monkeypatch):
    queued = await enqueue(runtime)
    run, task = await rows(runtime, queued['id'])
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
    run, task = await rows(runtime, queued['id'])
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
async def test_worker_token_failure_keeps_recovery_guard_and_never_retries(runtime, monkeypatch, failure):
    monkeypatch.setattr(tokens, 'settings', service_settings.get_settings().api.model_copy(update={
        'llm_token_buffer_max_items':1, 'llm_token_flush_characters':1,
        'llm_token_enqueue_timeout_seconds':.03,
    }))
    stopped = asyncio.Event()
    async def append(*args):
        if failure == 'write_error':
            raise OSError('simulated writer failure')
        await asyncio.Event().wait()
    monkeypatch.setattr(tokens.LLMTokenEventBuffer, '_append', append)
    async def graph(**kwargs):
        manager = AsyncCallbackManagerForLLMRun(run_id=uuid4(),
            handlers=kwargs['callbacks'], inheritable_handlers=[])
        try:
            await manager.on_llm_new_token('a')
            await manager.on_llm_new_token('b')
            await asyncio.Event().wait()
        finally:
            stopped.set()
    monkeypatch.setattr(runs, 'ainvoke_user_turn', graph)
    queued = await enqueue(runtime)
    with pytest.raises(ExecutionNeedsRecovery):
        await asyncio.wait_for(worker.execute_claimed(await worker.claim_one()), 2)
    assert stopped.is_set()
    run, task = await rows(runtime, queued['id'])
    assert task.recovery_required and task.lock_token is not None
    assert run.failure['code'] == 'RUN_RECOVERY_REQUIRED'
    assert not run.failure['retry_scheduled'] and run.status == AgentRunStatus.RUNNING
    async with runtime.factory() as db:
        events = await TaskEventService.list_after_public_run(db, run_id=run.public_run_id, sequence=0, limit=100)
    assert not any(event.event_type in ('task.success','task.retry_scheduled') for event in events)
    with pytest.raises(ExecutionNeedsRecovery):
        await worker.claim_one()
