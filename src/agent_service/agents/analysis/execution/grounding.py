"""Internal fact selectors and deterministic rendering; not a public Run contract."""
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr
from agent_service.runtime.session_analysis import analysis_for_owner
from .report import cell


class FactReference(BaseModel):
    model_config = ConfigDict(extra='forbid')
    step_id: str = Field(min_length=1, max_length=100)
    # Paths address the public summary after unwrapping typed dict/list/tuple items.
    # String keys and nonnegative list indices remain distinct (e.g. key "0").
    path: list[StrictStr | StrictInt] = Field(max_length=20)


class AnswerGrounding(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scope: Literal['general', 'analysis']
    source_run_id: str | None = None
    evidence_steps: list[str] = Field(default_factory=list, max_length=32)
    facts: list[FactReference] = Field(default_factory=list, max_length=128)


def completed_context(context, max_chars):
    record = analysis_for_owner(getattr(context, 'session_analysis_context', None),
        {k:getattr(context, k, '') for k in ('user_id', 'project_id', 'session_id')}, max_chars)
    return record['payload'] if record else None


def validate_interpretation(text, observations, *, tables=False):
    """Check copied numerals, not the truth of natural-language interpretations."""
    for observation in observations:
        for key in ('step_id', 'tool_id'):
            identifier = observation.get(key, '')
            if identifier and not identifier.isdecimal():
                text = re.sub(r'(?<!\w)'+re.escape(identifier)+r'(?!\w)', '', text)
    matches = list(re.finditer(r'\d+', text))
    if matches or (not tables and re.search(r'^\s*\|', text, re.MULTILINE)):
        fragments = [text[max(0,m.start()-12):m.end()+12] for m in matches[:8]]
        raise ValueError('Interpretation must not copy numeric values, calculations or numbered headings. '
            'Write qualitative interpretation only. Put desired exact values in grounding.facts selectors; '
            'the server appends the facts table. Remove every copied number, percentage, sample and numbered heading. '
            'Offending fragments: '+repr(fragments))


def fact_value(observation, path):
    if observation.get('status') != 'SUCCEEDED' or observation.get('incomplete') or observation.get('summary_omitted') or observation.get('summary') is None:
        raise ValueError('Fact must reference a complete successful observation with a retained summary')
    value = observation.get('summary')
    def unwrap(item):
        while isinstance(item, dict) and item.get('type') in {'dict', 'list', 'tuple'} and 'items' in item:
            # A retained leaf in a truncated collection is exact, but not a population statistic.
            item = item['items']
        return item
    for key in path:
        value = unwrap(value)
        if isinstance(key, str) and isinstance(value, dict) and key in value:
            value = value[key]
        elif type(key) is int and isinstance(value, list) and 0 <= key < len(value):
            value = value[key]
        else:
            raise ValueError('Fact path is absent; use exact public summary keys or nonnegative list indices')
    value = unwrap(value)
    scalar = lambda v: v is None or type(v) in (str, int, float, bool)
    if not scalar(value) and not (isinstance(value, list) and len(value) <= 16 and all(scalar(v) for v in value)):
        raise ValueError('Select a scalar fact or a small scalar array, never a whole summary object')
    rendered = cell(value)
    if len(rendered) > 800:
        raise ValueError('Selected fact is too large; select a smaller field')
    return rendered


def grounded_message(reply, context, *, max_chars=16000):
    """Used both by outer validation middleware and the graph publication boundary."""
    evidence = completed_context(context, max_chars)
    grounding = getattr(reply, 'grounding', None)
    if reply.kind != 'answer':
        if grounding is not None:
            raise ValueError('Plan proposals do not carry completed-answer grounding')
        return reply.message
    if evidence and grounding is None:
        raise ValueError('Answer with session evidence requires grounding: analysis for results, general for unrelated FAQ')
    if grounding is None:
        return reply.message
    if grounding.scope == 'general':
        if grounding.source_run_id is not None or grounding.evidence_steps or grounding.facts:
            raise ValueError('General FAQ must not cite analysis evidence')
        if '{{fact:' in reply.message:
            raise ValueError('General FAQ cannot contain fact placeholders')
        return reply.message
    if evidence is None or grounding.source_run_id != evidence['source_run_id']:
        raise ValueError('Use the exact source_run_id of this owner’s retained analysis')
    observations = {o['step_id']:o for o in evidence['observations']}
    allowed = {k for k,o in observations.items() if o['status']=='SUCCEEDED' and not o.get('incomplete') and not o.get('summary_omitted') and o.get('summary') is not None}
    if len(set(grounding.evidence_steps)) != len(grounding.evidence_steps) or not set(grounding.evidence_steps) <= allowed:
        raise ValueError('Cite exact complete successful Step IDs, not Tool names, missing or failed Steps: '+str(sorted(allowed)))
    # Qualitative prose and facts are separate; editing prose cannot shift reference indices.
    validate_interpretation(reply.message, list(observations.values()))
    if '{{fact:' in reply.message:
        raise ValueError('Do not put fact placeholders in message; the server renders grounding.facts separately')
    rows = []
    for fact in grounding.facts:
        if fact.step_id not in grounding.evidence_steps:
            raise ValueError('Every fact Step must be included in evidence_steps')
        value = fact_value(observations[fact.step_id], fact.path)
        label = fact.step_id + ('.' + '.'.join(str(k) for k in fact.path) if fact.path else '')
        rows.append('| '+cell(label)+' | '+value+' |')
    text = reply.message
    if rows:
        text += '\n\n## 확인된 출력값\n\n| 출력 항목 | 실제 값 |\n|---|---|\n'+'\n'.join(rows)
    # The public answer remains Markdown text, and SSE/history/GET use exactly this text.
    cited = ', '.join(cell(s) for s in grounding.evidence_steps) or '인용 가능한 성공 관찰 없음'
    text += '\n\n---\n근거 Step: '+cited
    text += '\n해석 범위: 저장된 출력에 근거한 설명이며, 원인이나 전체 분포가 검증되었다는 뜻은 아닙니다.'
    if evidence.get('status') == 'analysis_failed':
        text += '\n이전 분석은 실패했습니다. 성공한 부분의 관찰을 전체 분석 성공으로 해석하지 마세요.'
    def limited(value):
        if isinstance(value, dict):
            return bool(value.get('truncated')) or any(limited(v) for v in value.values())
        return isinstance(value, list) and any(limited(v) for v in value)
    if any(evidence.get(k) for k in ('omitted_observations','omitted_step_outcomes')) or any(o.get('summary_omitted') or o.get('incomplete') or limited(o.get('summary')) for o in observations.values()):
        text += '\n일부 관찰이 생략되거나 제한되어 있습니다. 표시되지 않은 결과를 확인한 것으로 간주하지 마세요.'
    if len(text)>24000:
        raise ValueError('Rendered answer exceeds its bounded text budget')
    return text
