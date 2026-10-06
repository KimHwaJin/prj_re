"""Executor HTTP request/receipt schemas shared without importing an Agent."""

from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExecutorLifecycle(StrictModel):
    operation_mode: Literal["SINGLE", "MULTI"]
    operation_wait_timeout_seconds: int | None = Field(default=None, gt=0)


class ExecutorActor(StrictModel):
    type: Literal["AGENT"] = "AGENT"
    id: str = Field(min_length=1)


class ExecutorTrigger(StrictModel):
    type: Literal["INTERACTIVE"] = "INTERACTIVE"
    actor: ExecutorActor


class ExecutorRuntime(StrictModel):
    type: Literal["JUPYTER"] = "JUPYTER"
    profile: str = Field(min_length=1)


class ExecutorContext(StrictModel):
    user_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    workflow_id: str = Field(min_length=1)


class ExecutorPathSource(StrictModel):
    type: Literal["PATH"] = "PATH"
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_source_path(self) -> "ExecutorPathSource":
        path = PurePosixPath(self.path)
        if path.is_absolute() or ".." in path.parts or "\\" in self.path:
            raise ValueError("source.path must be a relative shared-PV path")
        if not self.path.lower().endswith(".py"):
            raise ValueError("source.path must point to a Python file")
        return self


class ExecutorInlineSource(StrictModel):
    type: Literal["INLINE"] = "INLINE"
    content: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_content(self) -> "ExecutorInlineSource":
        if not self.content.strip():
            raise ValueError("source.content must not be blank")
        return self


class ExecutorStepPayload(StrictModel):
    type: Literal["PYTHON_EXECUTE"] = "PYTHON_EXECUTE"
    source: ExecutorPathSource | ExecutorInlineSource


class ExecutorStepLineage(StrictModel):
    skill_name: str | None = None
    tool_name: str | None = None
    input_parameters: dict[str, Any] = Field(default_factory=dict)


class ExecutorSourceStep(StrictModel):
    sequence: int = Field(ge=0)
    payload: ExecutorStepPayload
    step_timeout_seconds: int | None = Field(default=None, gt=0)
    lineage: ExecutorStepLineage


class ExecutorSourceSpec(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    steps: list[ExecutorSourceStep] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_steps(self) -> "ExecutorSourceSpec":
        sequences = [step.sequence for step in self.steps]
        if sequences != list(
            range(sequences[0], sequences[0] + len(self.steps))
        ):
            raise ValueError("source step sequence must be contiguous")
        paths = [
            step.payload.source.path
            for step in self.steps
            if isinstance(step.payload.source, ExecutorPathSource)
        ]
        if len(paths) != len(set(paths)):
            raise ValueError("payload paths must be unique")
        return self


class ExecutorOperation(StrictModel):
    operation_timeout_seconds: int | None = Field(default=None, gt=0)
    spec: ExecutorSourceSpec
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutorRequestBody(StrictModel):
    idempotency_key: str = Field(min_length=1)
    lifecycle: ExecutorLifecycle
    trigger: ExecutorTrigger
    runtime: ExecutorRuntime
    context: ExecutorContext
    operation: ExecutorOperation

    @model_validator(mode="after")
    def validate_operation_mode(self) -> "ExecutorRequestBody":
        if self.operation.spec.steps[0].sequence != 0:
            raise ValueError(
                "initial Execution Step sequence must start from 0"
            )
        wait_timeout = self.lifecycle.operation_wait_timeout_seconds
        operation_timeout = self.operation.operation_timeout_seconds
        if self.lifecycle.operation_mode == "SINGLE":
            if wait_timeout is not None:
                raise ValueError(
                    "SINGLE executor request must not set "
                    "operation_wait_timeout_seconds"
                )
        else:
            if wait_timeout is None:
                raise ValueError(
                    "MULTI executor request requires "
                    "operation_wait_timeout_seconds"
                )
            if operation_timeout is None:
                raise ValueError(
                    "MULTI executor request requires operation_timeout_seconds"
                )
        return self


class ExecutorContinueRequestBody(StrictModel):
    idempotency_key: str = Field(min_length=1)
    expected_version: int = Field(ge=0)
    operation_timeout_seconds: int | None = Field(default=None, gt=0)
    spec: ExecutorSourceSpec
    metadata: dict[str, Any] = Field(default_factory=dict)
    actor: ExecutorActor


class ExecutorFinalizeRequestBody(StrictModel):
    idempotency_key: str = Field(min_length=1)
    expected_version: int = Field(ge=0)
    actor: ExecutorActor


class ExecutorCancelRequestBody(StrictModel):
    idempotency_key: str = Field(min_length=1)
    reason: str | None = Field(default=None, max_length=2000)
    actor: ExecutorActor


class ExecutorResponseOperationStep(StrictModel):
    sequence: int = Field(ge=0)
    step_id: str = Field(min_length=1)


class ExecutorResponseOperation(StrictModel):
    operation_id: str = Field(min_length=1)
    steps: list[ExecutorResponseOperationStep] = Field(default_factory=list)


class ExecutorResponseState(StrictModel):
    status: Literal[
        "QUEUED",
        "DISPATCHED",
        "RUNNING",
        "WAITING_FOR_OPERATION",
        "FINALIZING",
        "CANCEL_REQUESTED",
        "CANCELLED",
        "SUCCEEDED",
        "FAILED",
    ]
    version: int = Field(ge=0)


class ExecutorSubmitResponse(StrictModel):
    execution_id: str = Field(min_length=1)
    operation: ExecutorResponseOperation
    state: ExecutorResponseState
    created_by_type: Literal["AGENT", "USER", "BATCH"] | None = None
    created_by: str | None = None
    updated_by_type: Literal["AGENT", "USER", "BATCH"] | None = None
    updated_by: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
