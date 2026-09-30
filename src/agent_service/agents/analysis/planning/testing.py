"""Explicit MODEL_PROVIDER=mock fixture, never a production model fallback."""
import asyncio
from importlib.resources import files
import json

from agent_service.agents.analysis.agent_builders.conversation.agent import reply_schema


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
