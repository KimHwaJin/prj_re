"""Project checkpointed public events atomically; no database access in nodes."""
from uuid import UUID

from api_service.schemas.common.message_schema import MessageCreate
from api_service.services.agent_run_log_service import AgentRunLogService
from api_service.services.message_service import MessageService
from api_service.services.run_service import RunService
from api_service.services import resource_lifecycle


async def persist_plan_events(db, state, context):
    events = state.get('public_events', [])
    if not events:
        return []
    owners = sorted({UUID(event['owner_run_id']) for event in events})
    try:
        # Same lock order as GraphResultBatch: Run barrier before Session/Task rows.
        for owner in owners:
            await AgentRunLogService.lock_run(db, owner)
        session = None
        if any(e['envelope']['type'] == 'message.completed' for e in events):
            session = await resource_lifecycle.lock_session(
                db, context.user_id, UUID(context.session_id),
                expected_project_id=UUID(state['project_id']), for_update=True,
            )
        persisted = []
        for event in events:
            envelope = event['envelope']
            owner = UUID(event['owner_run_id'])
            if envelope['type'] == 'message.completed':
                data = envelope['data']
                content = data['content']
                text = '\n'.join(p['text'] for p in content if p['type'] == 'text')
                role = 'agent' if data['channel'] == 'commentary' else data['role']
                message = await MessageService._create_locked(db, session, MessageCreate(
                    session_id=session.session_id, project_id=session.project_id,
                    message_type=role, content=content, content_text=text,
                    client_request_id=UUID(event['event_id']),
                    metadata={'source': 'dtest-agent', 'run_id': envelope['run_id'],
                              'channel': data['channel'], 'event_id': event['event_id']},
                ), commit=False)
                if data['role'] == 'user':
                    await RunService.attach_trigger_message(db, run_id=owner,
                        message_id=message.message.message_id, commit=False)
            log = await AgentRunLogService.create(
                db, run_id=owner, event_key='public:' + event['event_id'],
                agent_name='analysis_conversation', node='planning', event=envelope['type'],
                kind='public_event', payload=envelope, commit=False,
            )
            persisted.append({'event_id': event['event_id'], 'log_id': str(log.log_id)})
        await db.commit()
        return persisted
    except BaseException:
        await db.rollback()
        raise
