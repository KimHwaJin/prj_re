"""Dependency readiness and permanent command outcomes are distinct."""
import asyncio
import pytest
from sqlalchemy.exc import SQLAlchemyError
from psycopg_pool import PoolTimeout
from redis.exceptions import ConnectionError
from dtest.application.runs.commands.outcome import event_outcome
from dtest.contracts.events import DeferEvent,IgnoreEvent,RejectEvent
from dtest.contracts.execution import ExecutionNeedsRecovery

@pytest.mark.parametrize('error,state,spent', [
    (None,'DONE',0),(DeferEvent('not ready'),'READY',0),
    (SQLAlchemyError('db'),'READY',0),(PoolTimeout('pool'),'READY',0),
    (ConnectionError('redis'),'READY',0),(IgnoreEvent('duplicate'),'IGNORED',0),
    (RejectEvent('invalid'),'FAILED',1),(ValueError('handler'),'READY',1),
    (ExecutionNeedsRecovery('uncertain'),'RECOVERY',0),(asyncio.CancelledError(),'RECOVERY',0),
])
def test_event_retry_budget(error,state,spent):
    assert event_outcome(error,0,5)==(state,spent)

def test_business_retry_exhaustion_is_terminal_but_deferral_is_not():
    assert event_outcome(ValueError('handler'),4,5)==('FAILED',1)
    assert event_outcome(DeferEvent('handoff'),100,5)==('READY',0)


@pytest.mark.asyncio
async def test_executor_ingress_does_not_construct_another_graph_dispatcher():
    from dtest.settings.loader import load_settings
    from dtest.worker_service.executor_events.runtime import ExecutorWorker
    worker=ExecutorWorker(load_settings(config={},environ={}).worker,{'execution.completed'})
    try:
        assert len(worker.consumers)==1
        assert not any(hasattr(worker,attr) for attr in ('dispatcher','guard','outbox','handlers'))
    finally:
        await worker.http.aclose()
        await worker.redis.aclose()
        await worker.pool.close()
