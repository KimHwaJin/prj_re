"""Unified start/resume request; attachment references leave room for multimodal input."""
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator
from service_contracts.plan_interaction import StrictModel, ResumeCommand


class TextContent(StrictModel):
    type: Literal['text']
    text: str = Field(min_length=1, max_length=12000)


class ImageContent(StrictModel):
    type: Literal['image']
    file_id: UUID


class FileContent(StrictModel):
    type: Literal['file']
    file_id: UUID


Content = Annotated[TextContent | ImageContent | FileContent, Field(discriminator='type')]


class ChatInput(StrictModel):
    content: list[Content] = Field(min_length=1, max_length=20)


class RunRequest(StrictModel):
    input: ChatInput | None = None
    command: ResumeCommand | None = None
    run_id: UUID | None = None
    resume_token: UUID | None = None
    main_model_name: str | None = Field(default=None, min_length=1, max_length=128, pattern=r'^[a-zA-Z0-9_.-]+$')

    @model_validator(mode='after')
    def shape(self):
        if (self.input is None) == (self.command is None):
            raise ValueError('Exactly one of input or command is required')
        if self.command is not None:
            if self.run_id is None or self.resume_token is None:
                raise ValueError('Resume requires run_id and resume_token')
            if self.main_model_name is not None:
                raise ValueError('The model is pinned when a Run starts')
        elif self.run_id is not None or self.resume_token is not None:
            raise ValueError('A new request must omit run_id and resume_token')
        return self
