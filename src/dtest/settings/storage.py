"""File storage owned by infrastructure, loaded with the service snapshot."""

from pathlib import Path
from pydantic import BaseModel, ConfigDict


class StorageSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    workflow_storage_root: Path = Path("var/workflows")
