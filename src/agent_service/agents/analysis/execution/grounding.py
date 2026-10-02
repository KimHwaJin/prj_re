"""Internal fact selectors and deterministic rendering; not a public Run contract."""
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr
from agent_service.runtime.session_analysis import analysis_for_owner, encoded
from .report import cell, render_evidence_markdown


# Upper bound for the transient model catalogue; stored evidence is not trimmed here.
MAX_CATALOG_FACTS = 512


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
    # Ephemeral references in this owner's bounded evidence; never persisted IDs.
    fact_ids: list[StrictStr] = Field(default_factory=list, max_length=128,
        description='Select exact IDs from the current analysis.fact_catalog; values are rendered by the server. Do not copy numbers into message.')


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


def limited_summary(value):
    if isinstance(value, dict):
        return bool(value.get('truncated')) or any(limited_summary(v) for v in value.values())
    return isinstance(value, list) and any(limited_summary(v) for v in value)


def compact_evidence(evidence, *, max_chars=16000):
    """A bounded model view and private ID -> exact Step/path map, rebuilt per call.

    Stored observations stay untouched. Only an exactly reconstructed server
    evidence suffix is removed from the report; model-authored prose is retained.
    The catalogue reuses fact_value's completeness, path and scalar size rules.
    """
    observations = list({o['step_id']:o for o in evidence['observations']}.values())
    result = {**evidence, 'observations':[{**{k:v for k,v in o.items() if k != 'summary'}, 'summary_limited':limited_summary(o.get('summary'))} for o in observations],
              'fact_catalog':{}, 'fact_catalog_limited':False}
    report = dict(evidence.get('report', {}))
    successful = [o for o in evidence['observations'] if o.get('status') == 'SUCCEEDED']
    suffix = '\n\n' + render_evidence_markdown(successful)
    excerpt = report.get('excerpt', '')
    if excerpt.endswith(suffix):
        report.update(excerpt=excerpt[:-len(suffix)], server_evidence_table_in_catalog=True)
    result['report'] = report
    if len(encoded(result)) > max_chars:
        # Rare metadata-heavy records retain the old bounded view and selectors.
        return evidence, {}

    def leaves(value, path):
        while isinstance(value, dict) and value.get('type') in {'dict','list','tuple'} and 'items' in value:
            value = value['items']
        if len(path) > 20:
            return
        scalar = lambda v: v is None or type(v) in (str, int, float, bool)
        if scalar(value) or (isinstance(value, list) and len(value) <= 16 and all(scalar(v) for v in value)):
            yield path, value
        elif isinstance(value, dict):
            for key, item in value.items():
                if isinstance(key, str):
                    yield from leaves(item, [*path, key])
        elif isinstance(value, list):
            for index, item in enumerate(value):
                yield from leaves(item, [*path, index])

    def identifier(index):
        # Alphabetic IDs avoid encouraging numbered headings in qualitative prose.
        letters = ''
        while True:
            index, digit = divmod(index, 26)
            letters = chr(ord('a') + digit) + letters
            if not index:
                return 'f_' + letters
            index -= 1

    registry = {}
    for observation in observations:
        if observation.get('status') != 'SUCCEEDED' or observation.get('incomplete') or observation.get('summary_omitted') or observation.get('summary') is None:
            continue
        for path, value in leaves(observation['summary'], []):
            if len(registry) == MAX_CATALOG_FACTS:
                result['fact_catalog_limited'] = True
                return result, registry
            try:
                fact_value(observation, path)
            except ValueError:
                result['fact_catalog_limited'] = True
                continue
            key = identifier(len(registry))
            reference = FactReference(step_id=observation['step_id'], path=path)
            # Group by Step; do not repeat its ID or a long Step/path per fact.
            row = {'label':'.'.join(str(k) for k in path) if path else '(summary)', 'value':value}
            group = {**result['fact_catalog'].get(reference.step_id, {}), key:row}
            candidate = {**result, 'fact_catalog':{**result['fact_catalog'], reference.step_id:group}}
            if len(encoded(candidate)) <= max_chars:
                result = candidate
                registry[key] = reference
            else:
                result['fact_catalog_limited'] = True
    return result, registry


def compact_evidence_view(evidence, *, max_chars=16000):
    """Role-owned projection injected into the reusable session middleware."""
    return compact_evidence(evidence, max_chars=max_chars)[0]


def selected_facts(grounding, evidence, *, max_chars=16000):
    """Resolve model-selected IDs only against the current owner/source record."""
    if grounding.facts and grounding.fact_ids:
        raise ValueError('Use fact_ids or legacy facts, never both')
    if not grounding.fact_ids:
        return grounding.facts, False
    if len(set(grounding.fact_ids)) != len(grounding.fact_ids):
        raise ValueError('fact_ids must be distinct')
    view, registry = compact_evidence(evidence, max_chars=max_chars)
    if not set(grounding.fact_ids) <= registry.keys():
        raise ValueError('Choose exact fact_ids from this source analysis fact_catalog')
    return [registry[key] for key in grounding.fact_ids], view.get('fact_catalog_limited', False)


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
        if grounding.source_run_id is not None or grounding.evidence_steps or grounding.facts or grounding.fact_ids:
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
    references, catalog_limited = selected_facts(grounding, evidence, max_chars=max_chars)
    for fact in references:
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
    if catalog_limited or any(evidence.get(k) for k in ('omitted_observations','omitted_step_outcomes')) or any(o.get('summary_omitted') or o.get('incomplete') or limited_summary(o.get('summary')) for o in observations.values()):
        text += '\n일부 관찰이 생략되거나 제한되어 있습니다. 표시되지 않은 결과를 확인한 것으로 간주하지 마세요.'
    if len(text)>24000:
        raise ValueError('Rendered answer exceeds its bounded text budget')
    return text
