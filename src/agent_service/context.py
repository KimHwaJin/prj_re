"""Per-invocation context. Never put clients or repositories in graph state."""
from dataclasses import dataclass, field
from typing import Protocol


class ProjectMemory(Protocol):
    """A project-scoped repository supplied by the service; no global keys."""
    async def read(self, project_id: str) -> dict: ...
    async def apply(self, changes: list[dict]) -> dict: ...


@dataclass(frozen=True)
class AgentContext:
    user_id: str = ""
    project_id: str = ""
    session_id: str = ""
    project_system_prompt: str = ""
    project_prompt_version: int | None = None
    model_name: str = ""
    model_selection: dict[str, str] | None = None
    # Bounded completed evidence for this exact user/project/session, never shared Agent state.
    session_analysis_context: dict | None = field(default=None, repr=False, compare=False)
    project_memory: ProjectMemory | None = field(default=None, repr=False, compare=False)
    project_memory_auto_write: bool = False
