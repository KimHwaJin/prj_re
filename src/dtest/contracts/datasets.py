"""Trusted read-only dataset declarations; file access remains in Jupyter."""

from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DatasetDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    runtime_path: str
    scope: Literal["GLOBAL", "USER", "PROJECT", "SESSION"]
    owner_user_id: str | None = None
    project_id: str | None = None
    session_id: str | None = None

    @model_validator(mode="after")
    def valid(self):
        path = PurePosixPath(self.runtime_path)
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError(
                "Dataset runtime_path must be an absolute Jupyter path"
            )
        if self.scope != "GLOBAL" and not self.owner_user_id:
            raise ValueError("Scoped datasets require owner_user_id")
        if self.scope in {"PROJECT", "SESSION"} and not self.project_id:
            raise ValueError("Dataset requires project_id")
        if self.scope == "SESSION" and not self.session_id:
            raise ValueError("Dataset requires session_id")
        return self
