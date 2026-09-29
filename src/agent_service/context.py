"""Per-invocation context. Never put clients or repositories in graph state."""
from dataclasses import dataclass, field
from typing import Protocol


class ProjectMemory(Protocol):
    """A project-scoped repository supplied by the service; no global keys."""
    async def read(self, project_id: str) -> str: ...
    async def write(self, project_id: str, content: str, *, source_id: str) -> None: ...


@dataclass(frozen=True)
class AgentContext:
    user_id: str = ""
    project_id: str = ""
    session_id: str = ""
    project_system_prompt: str = ""
    project_prompt_version: int | None = None
    model_name: str = ""
    model_selection: dict[str, str] | None = None
    project_memory: ProjectMemory | None = field(default=None, repr=False, compare=False)
