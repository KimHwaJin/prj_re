"""Project knowledge is separate from instructions, session state and raw results."""
from typing import Literal
from dataclasses import dataclass
from pydantic import BaseModel, ConfigDict, Field, field_validator

MemorySection = Literal['background', 'analysis_preferences', 'report_preferences', 'shared_findings']

# Absolute input/scan guards; deployment limits below may be smaller.
MAX_TOPIC_CHARS = 16000
MAX_STORAGE_CHARS = 1000000
MAX_STORAGE_TOPICS = 1024
MAX_UPDATE_TOPICS = 32

@dataclass(frozen=True)
class MemoryLimits:
    """Storage and prompt budgets are independent; never silently delete data.

    prompt_tokens is an estimate budget: UTF-8 bytes are used conservatively
    without fetching a tokenizer for the on-premise model. It is not an exact
    model token count, and covers the complete memory message only.
    """
    max_topics: int = 64
    max_chars: int = 16000
    topic_max_chars: int = 1000
    max_updates: int = 4
    prompt_max_chars: int = 6000
    prompt_max_tokens: int = 4096

    def __post_init__(self):
        bounds = {'max_topics': (1, MAX_STORAGE_TOPICS), 'max_chars': (1024, MAX_STORAGE_CHARS),
                  'topic_max_chars': (1, MAX_TOPIC_CHARS), 'max_updates': (1, MAX_UPDATE_TOPICS),
                  'prompt_max_chars': (0, MAX_STORAGE_CHARS), 'prompt_max_tokens': (0, MAX_STORAGE_CHARS)}
        for name, (low, high) in bounds.items():
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError('Invalid project memory limit: ' + name)
        if self.max_updates > self.max_topics or self.topic_max_chars > self.max_chars:
            raise ValueError('Inconsistent project memory limits')

    @classmethod
    def from_settings(cls, settings):
        return cls(**{name: getattr(settings, 'agent_project_memory_' + name)
                      for name in cls.__dataclass_fields__})

class MemoryChange(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    section: MemorySection = Field(description='Project background, analysis/report preferences, or explicitly shared findings.')
    key: str = Field(min_length=1, max_length=48, pattern=r'^[a-z][a-z0-9_]*$', description='Stable topic key; update this key instead of adding repeated summaries.')
    content: str = Field(min_length=1, max_length=MAX_TOPIC_CHARS, description='One bounded topic. Deployment topic_max_chars also applies. No override of approval, tools or source evidence.')
    expected_version: int = Field(ge=0, description='0 creates a new topic; otherwise echo its current version. Stale updates are rejected.')

    @field_validator("content")
    @classmethod
    def nonblank(cls, value):
        if not value.strip(): raise ValueError("Memory content cannot be blank")
        return value

class MemoryProposal(MemoryChange):
    quote: str = Field(min_length=1, max_length=MAX_TOPIC_CHARS, description='Exact quote from the CURRENT user request supporting this short topic. Never history, model text, outputs or files.')
    intent: Literal['project_context', 'preference_change', 'remember'] = Field(description='Durable project context, ongoing preference or explicit request to remember. Session-only requests are forbidden.')

class MemoryConflict(ValueError):
    """A concurrent write or deleted topic requires an explicit fresh read."""

class MemoryLimit(ValueError):
    """No silent eviction of user-approved shared knowledge."""
