"""Per-invocation context. Never put clients or repositories in graph state."""
from dataclasses import dataclass, field
from typing import Protocol
from dtest.contracts.project_memory import MemoryLimits


class ProjectMemoryPolicy(Protocol):
    """Owner/source/version policy around runtime.store; not a storage backend."""
    async def read(self, store) -> dict: ...
    async def apply(self, store, changes: list[dict]) -> dict: ...


@dataclass(frozen=True)
class AgentContext:
    user_id: str = ""
    project_id: str = ""
    session_id: str = ""
    project_system_prompt: str = ""
    project_prompt_version: int | None = None
    model_name: str = ""
    recursion_limit: int = 100
    model_selection: dict[str, str] | None = None
    # Bounded completed evidence for this exact user/project/session, never shared Agent state.
    session_analysis_context: dict | None = field(default=None, repr=False, compare=False)
    project_memory_policy: ProjectMemoryPolicy | None = field(default=None, repr=False, compare=False)
    project_memory_auto_write: bool = False
    project_memory_limits: MemoryLimits = field(default_factory=MemoryLimits)
    project_memory_request: str = field(default='', repr=False, compare=False)
