"""One bounded project reference document, independent of instructions/history."""
from dataclasses import dataclass
from typing import Literal
import re
from pydantic import BaseModel, ConfigDict, Field, field_validator

MemorySection = Literal['background', 'analysis_preferences', 'report_preferences', 'shared_findings']
SECTION_TITLES = {
    'background': '프로젝트 배경',
    'analysis_preferences': '분석 선호',
    'report_preferences': '보고서 선호',
    'shared_findings': '공유할 주요 발견',
}
MAX_STORAGE_CHARS = 1_000_000
MAX_PATCH_CHARS = 16000
MAX_UPDATE_SECTIONS = len(SECTION_TITLES)

@dataclass(frozen=True)
class MemoryLimits:
    """Separate storage/prompt budgets; no silent eviction.

    prompt_max_tokens uses a conservative UTF-8 byte estimate, not a model
    tokenizer. patch_max_chars bounds Agent replacements/quotes, not manual
    edits or the old text copied from the snapshot.
    """
    max_chars: int = 16000
    patch_max_chars: int = 4000
    max_updates: int = 4
    prompt_max_chars: int = 6000
    prompt_max_tokens: int = 4096

    def __post_init__(self):
        bounds = {'max_chars': (1024, MAX_STORAGE_CHARS), 'patch_max_chars': (1, MAX_PATCH_CHARS),
                  'max_updates': (1, MAX_UPDATE_SECTIONS), 'prompt_max_chars': (0, MAX_STORAGE_CHARS),
                  'prompt_max_tokens': (0, MAX_STORAGE_CHARS)}
        for name, (low, high) in bounds.items():
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError('Invalid project memory limit: ' + name)
        if self.patch_max_chars > self.max_chars:
            raise ValueError('Memory patch limit exceeds document limit')

    @classmethod
    def from_settings(cls, settings):
        return cls(**{name: getattr(settings, 'agent_project_memory_' + name)
                      for name in cls.__dataclass_fields__})

class MemoryPatch(BaseModel):
    """Internal Agent update; no public memory IDs or arbitrary topic keys."""
    model_config = ConfigDict(extra='forbid', strict=True)
    section: MemorySection = Field(description='Fixed Markdown section; shared_findings is manual-only.')
    old_text: str = Field(max_length=MAX_STORAGE_CHARS, description='Copy the COMPLETE current section body exactly, without heading or outer whitespace. Empty only for an absent/empty section.')
    content: str = Field(min_length=1, max_length=MAX_PATCH_CHARS, description='New complete section body; preserve still-valid existing information and add no unsupported claims.')
    expected_version: int = Field(ge=0, description='Echo the PROJECT DOCUMENT version in the reference, including after reset.')

    @field_validator('content')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('Memory replacement cannot be blank')
        return value

class MemoryProposal(MemoryPatch):
    quote: str = Field(min_length=1, max_length=MAX_PATCH_CHARS, description='Exact CURRENT user quote supporting the change; never history, observations or files.')
    intent: Literal['project_context', 'preference_change', 'remember'] = Field(description='Durable context, ongoing preference or explicit remember intent.')

class MemoryConflict(ValueError):
    """A stale document or mismatched section must not be overwritten."""

class MemoryLimit(ValueError):
    """Over-budget memory needs explicit shortening, never silent truncation."""

@dataclass(frozen=True)
class MarkdownPart:
    section: str | None
    start: int
    body_start: int
    end: int
    text: str
    body: str


def _heading_spans(content: str):
    """Scan level-two headings and unfinished code fences without changing text.

    Arbitrary Markdown stays valid. Unknown headings/preamble are unclassified
    reference text. Duplicate known headings cannot be edited automatically.
    """
    headings = []
    fence_char, fence_size = None, 0
    offset = 0
    titles = {title: section for section, title in SECTION_TITLES.items()}
    for line in content.splitlines(keepends=True):
        fence = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line.rstrip('\r\n'))
        if fence_char:
            if fence and fence.group(1)[0] == fence_char and len(fence.group(1)) >= fence_size and not fence.group(2).strip():
                fence_char = None
        elif fence:
            fence_char, fence_size = fence.group(1)[0], len(fence.group(1))
        else:
            heading = re.match(r'^ {0,3}##[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$', line.rstrip('\r\n'))
            if heading:
                headings.append((offset, offset + len(line), titles.get(heading.group(1))))
        offset += len(line)
    return headings, fence_char is not None


def has_open_fence(content: str) -> bool:
    return _heading_spans(content)[1]


def markdown_parts(content: str) -> list[MarkdownPart]:
    headings, _ = _heading_spans(content)
    parts = []
    if not headings:
        return [MarkdownPart(None, 0, 0, len(content), content, content.strip())] if content else []
    if headings[0][0]:
        end = headings[0][0]
        parts.append(MarkdownPart(None, 0, 0, end, content[:end], content[:end].strip()))
    for index, (start, body_start, section) in enumerate(headings):
        end = headings[index + 1][0] if index + 1 < len(headings) else len(content)
        parts.append(MarkdownPart(section, start, body_start, end, content[start:end], content[body_start:end].strip()))
    return parts


def section_body(content: str, section: str) -> str:
    matches = [part for part in markdown_parts(content) if part.section == section]
    if len(matches) > 1:
        raise MemoryConflict('Memory section heading is duplicated; edit the document explicitly')
    return matches[0].body if matches else ''


def replace_section(content: str, patch: MemoryPatch) -> str:
    """Only the selected body changes; all other Markdown remains untouched."""
    matches = [part for part in markdown_parts(content) if part.section == patch.section]
    if len(matches) > 1:
        raise MemoryConflict('Memory section heading is duplicated; edit the document explicitly')
    part = matches[0] if matches else None
    if patch.old_text != (part.body if part else ''):
        raise MemoryConflict('Memory section no longer matches the proposed old text')
    if has_open_fence(patch.content):
        raise MemoryConflict('Agent replacement must close all Markdown code fences')
    if any(p.body_start > p.start for p in markdown_parts(patch.content)):
        raise MemoryConflict('Agent replacement must contain a section body, not level-two headings')
    if part is None:
        if has_open_fence(content):
            raise MemoryConflict('Cannot append a memory section inside an unfinished code fence')
        separator = '' if not content else ('\n' if content.endswith('\n') else '\n\n')
        return content + separator + '## ' + SECTION_TITLES[patch.section] + '\n' + patch.content.strip() + '\n'
    heading = content[part.start:part.body_start]
    separator = '' if heading.endswith(('\n', '\r')) else '\n'
    suffix = '\n\n' if part.end < len(content) else '\n'
    return content[:part.body_start] + separator + patch.content.strip() + suffix + content[part.end:]
