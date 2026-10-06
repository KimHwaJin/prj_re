"""Explicit MODEL_PROVIDER=mock fixture, never a production model fallback."""
import asyncio
from importlib.resources import files
import json

from dtest.agent_service.agents.analysis.agent_builders.conversation.agent import reply_schema


class MockConversation:
    def __init__(self, catalog, delay_ms=0):
        self.schema = reply_schema(catalog, 5)
        self.delay_ms = delay_ms
        self.calls = 0

    async def ainvoke(self, value, *, context=None):
        self.calls += 1
        if self.delay_ms:
            await asyncio.sleep(self.delay_ms / 1000)
        if value['request'].startswith('[answer]'):
            return self.schema(kind='answer', message='Mock 답변입니다.', plans=[])
        document = json.loads(files(__package__).joinpath('fixtures/quality-review.json').read_text())
        datasets = value.get('dataset_catalog', [])
        values = {'dataset': datasets[0]['dataset_id']} if datasets else {}
        return self.schema(kind='plans', message='등록된 Skill·Tool로 품질 분석 계획을 준비했습니다. 실행 전 계획을 확인해 주세요.',
                           plans=[{'definition': document, 'input_values': values}])


def mock_execution_role(role, payload):
    """Explicit mock provider only; its output still uses real supplied observations."""
    if role == 'repair':
        from dtest.contracts.execution_repair import RepairResponse
        return RepairResponse(can_repair=False,summary='명시적 mock은 수정안을 만들지 않습니다.',
            reason='No repair fixture configured; never fabricate a successful correction.',evidence_steps=payload['repair_context']['failed_step_ids'])
    if role == 'review':
        from dtest.agent_service.agents.analysis.agent_builders.execution_review.agent import ReviewResponse
        choices = []
        for item in payload['pending_decisions']:
            schema = item['output_schema']
            if schema.get('type') == 'boolean':
                value = True
            elif schema.get('enum'):
                value = schema['enum'][0]
            else:
                return ReviewResponse(choices=choices,needs_user_input=True,message='Mock 판단값을 사용자에게 확인합니다.')
            choices.append({'decision_id':item['id'],'value':value,'reason':'명시적 mock 실행 정책의 검증용 선택입니다.','evidence_steps':item['after_steps']})
        return ReviewResponse(choices=choices,needs_user_input=False,message='실행된 단계의 결과를 확인하고 다음 승인된 단계를 준비했습니다.')
    from dtest.agent_service.agents.analysis.agent_builders.execution_report.agent import ReportResponse
    facts = payload['observations']
    return ReportResponse(markdown='# 분석 결과\n\n명시적 mock 모델로 작성한 검증용 해석입니다. 실제 결과는 아래 실행 근거를 확인하세요.',
        evidence_steps=[o['step_id'] for o in facts if o['status']=='SUCCEEDED'])
