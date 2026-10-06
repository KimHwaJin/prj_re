"""Typed Agent settings and helpers for the central snapshot.

Source loading belongs to service_settings. Explicit mappings support isolated
graph tests without reading the process environment or local files.
"""

from __future__ import annotations

from pydantic.dataclasses import dataclass
from pydantic import AliasChoices, ConfigDict, Field, field_validator, model_validator
from pathlib import Path
from typing import Any, Literal, Mapping

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_EXECUTOR_SHARED_INPUT_ROOT = Path("/workspace/shared")
DEFAULT_MOCK_DATA_ROOT = Path("/workspace/pv")
MOCK_DATA_PATH_BY_TYPE = {
    "nce": "df_nce_wide_format.parquet",
    "wt_symbol": "df_wt_symbol_wide_format.parquet",
}


def resolve_mock_data_path(data_type: str) -> Path:
    """Resolve a mock path from the configured data root and type mapping."""
    normalized_type = str(data_type).strip().lower()
    try:
        filename = MOCK_DATA_PATH_BY_TYPE[normalized_type]
    except KeyError as error:
        supported = ", ".join(sorted(MOCK_DATA_PATH_BY_TYPE))
        raise ValueError(
            f"Unsupported mock data_type {normalized_type!r}; "
            f"supported: {supported}"
        ) from error
    from service_settings import get_settings
    mock_data_root = get_settings().mock_data_root
    return mock_data_root / "data" / filename


# Fixture for the retained legacy Workflow compiler; not a current Run/HITL input.
TEST_DATA_SELECTION = {
    "data_count": 2,
    "datasets": [
        {
            "role": "x",
            "data_type": "nce",
            "lot_cd": "6E2",
            "process": ["ALL"],
            "query_mode": "period",
            "start_dt": "2026-05-01",
            "end_dt": "2026-05-10",
            "limit": 100000000000,
            "transform_op": "pivot",
        },
        {
            "role": "y",
            "data_type": "wt_symbol",
            "lot_cd": "6E2",
            "process": ["PT1H"],
            "query_mode": "period",
            "start_dt": "2026-08-01",
            "end_dt": "2026-08-01",
            "limit": 1000001,
            "transform_op": "wt_fail_pivot",
        },
    ],
}


def build_langgraph_thread_id(session_id: str) -> str:
    """Build the checkpoint thread key for one conversation session."""
    normalized_session_id = session_id.strip()
    if not normalized_session_id:
        raise ValueError("session_id is required")
    return normalized_session_id


@dataclass(frozen=True, config=ConfigDict(populate_by_name=True, extra="forbid", allow_inf_nan=False))
class AgentSettings:
    """Typed Agent values. Sources and shared defaults are resolved by the service loader."""

    environment: str = Field(default="local", validation_alias="APP_ENV")
    model_provider: Literal["mock", "openai_compatible"] = "openai_compatible"
    model_name: str = Field(default="qwen38-27b-nvfp4", validation_alias=AliasChoices("MODEL_NAME", "PRIVATE_LLM_MODEL_NAME", "LLM_MODEL_NAME"))
    model_api_key: str | None = Field(default=None, validation_alias=AliasChoices("MODEL_API_KEY", "PRIVATE_LLM_API_KEY", "LLM_API_KEY"), repr=False)
    api_base_url: str | None = Field(default=None, validation_alias=AliasChoices("API_BASE_URL", "PRIVATE_LLM_ENDPOINT", "LLM_API_BASE_URL"))
    model_temperature: float = Field(default=0.2, validation_alias=AliasChoices("MODEL_TEMPERATURE", "LLM_TEMPERATURE"))
    model_timeout_seconds: float = Field(default=60, gt=0, validation_alias=AliasChoices("MODEL_TIMEOUT_SECONDS", "LLM_TIMEOUT_SECONDS"))
    model_max_retries: int = Field(default=0, ge=0, validation_alias=AliasChoices("MODEL_MAX_RETRIES", "LLM_MAX_RETRIES"))
    model_enable_thinking: bool | None = Field(default=None, validation_alias=AliasChoices("MODEL_ENABLE_THINKING", "LLM_ENABLE_THINKING"))
    model_structured_output_mode: Literal["prompt_json", "provider_json_schema"] = Field(default="prompt_json", validation_alias=AliasChoices("MODEL_STRUCTURED_OUTPUT_MODE", "LLM_STRUCTURED_OUTPUT_MODE"))
    model_mock_delay_ms: int = Field(default=0, ge=0, le=60000)
    # Built from the shared MODEL_CATALOG after source validation; not a YAML field.
    model_catalog: Any = Field(default=None, exclude=True, repr=False)

    checkpoint_db_uri: str = Field(default="postgresql://postgres:1234@127.0.0.1:5432/chat_app?sslmode=disable", validation_alias=AliasChoices("CHECKPOINT_DB_URI", "AGENT_CHECKPOINT_DATABASE_URL"), repr=False)
    checkpoint_setup_on_start: bool = True
    checkpoint_pool_min_size: int = Field(default=1, ge=1)
    checkpoint_pool_max_size: int = Field(default=4, ge=1)
    checkpoint_pool_timeout_seconds: float = Field(default=10, gt=0)
    strict_checkpoint_msgpack: bool = Field(default=True, validation_alias="LANGGRAPH_STRICT_MSGPACK")

    executor_base_url: str = Field(default="http://executor:8080", validation_alias=AliasChoices("EXECUTOR_BASE_URL", "EW_EXECUTOR_BASE_URL"))
    executor_tls_verify: bool = True
    executor_runtime_profile: str = "ml"
    # Omitted profiles resolve to the configured default; no live request per session.
    executor_runtime_profiles: tuple[str, ...] = ()
    executor_executions_path: str = Field(default="/api/v1/executions", validation_alias=AliasChoices("EXECUTOR_EXECUTIONS_PATH", "EXECUTOR_JOBS_PATH"))
    executor_operations_path: str = "/api/v1/executions/{execution_id}/operations"
    executor_execution_path: str = "/api/v1/executions/{execution_id}"
    executor_result_path: str = "/api/v1/executions/{execution_id}/result"
    executor_notebook_path: str = "/api/v1/executions/{execution_id}/notebook"
    executor_finalize_path: str = "/api/v1/executions/{execution_id}/finalize"
    executor_cancel_path: str = "/api/v1/executions/{execution_id}/cancel"
    executor_artifacts_path: str = "/api/v1/executions/{execution_id}/artifacts"
    executor_shared_input_root: Path = DEFAULT_EXECUTOR_SHARED_INPUT_ROOT
    executor_shared_result_root: Path = Path("/workspace/pv")
    executor_result_read_mode: Literal["API", "MANIFEST"] = "API"
    executor_source_type: Literal["PATH", "INLINE"] = "PATH"
    executor_report_source_type: Literal["PATH", "INLINE"] = "INLINE"
    executor_report_append_to_notebook: bool = True
    executor_timeout_seconds: float = Field(default=30, gt=0)
    executor_operation_timeout_seconds: int = Field(default=600, gt=0)
    executor_operation_wait_timeout_seconds: int = Field(default=600, ge=30)
    executor_submit_enabled: bool = False
    executor_http_max_connections: int = Field(default=8, ge=1)
    executor_http_connect_timeout_seconds: float = Field(default=5, gt=0)
    executor_http_pool_timeout_seconds: float = Field(default=5, gt=0)
    executor_http_max_response_bytes: int = Field(default=16 * 1024 * 1024, ge=1)
    data_mock: bool = False
    demo_artifacts_enabled: bool = True
    demo_artifacts_root: Path = PROJECT_ROOT / "demo_artifacts"

    phoenix_endpoint: str | None = None
    phoenix_project_name: str = "dtest-agent"
    phoenix_api_key: str | None = Field(default=None, repr=False)
    max_workflow_revisions: int = Field(default=10, ge=1)
    workflow_recommendation_enabled: bool = True
    workflow_similarity_score: float = Field(default=0.93, ge=0, le=1)
    # Combined recommended/generated plan count. Never generate filler alternatives.
    max_plan_candidates: int = Field(default=5, ge=1, le=20)
    agent_discovery_max_rounds: int = Field(default=4, ge=1, le=12)
    # Step budget per invocation, independent of waits and repair/operation limits.
    recursion_limit: int = Field(default=100, ge=1)
    active_multi_turn: bool = True
    # Previous public Run turns + current Run; same-Run HITL feedback stays together.
    set_max_history: int = Field(default=6, ge=0, le=100)
    active_trace: bool = True
    # Serialized latest analysis excerpt; 0 disables injection, not durable results.
    agent_session_analysis_max_chars: int = 16000
    agent_project_memory_mode: Literal["off", "manual", "auto_context"] = "manual"
    # Separate stored Markdown, patch, update and prompt budgets; no automatic eviction.
    agent_project_memory_max_chars: int = 16000
    agent_project_memory_patch_max_chars: int = 4000
    agent_project_memory_max_updates: int = 4
    agent_project_memory_prompt_max_chars: int = 6000
    agent_project_memory_prompt_max_tokens: int = 4096
    # Trusted dataset IDs -> Jupyter paths; never supplied by public request bodies.
    analysis_datasets: dict[str, dict] = Field(default_factory=dict, repr=False)
    agent_observation_max_chars: int = Field(default=16000, ge=1024, le=64000)
    agent_max_operations: int = Field(default=64, ge=1, le=256)
    # 0=off, 1=bindings, 2=tool fix, 3=registered replan, 4=execution-local code.
    agent_repair_level: int = Field(default=0, ge=0, le=4)
    agent_repair_level_limit: int = Field(default=4, ge=0, le=4)
    agent_max_repair_attempts: int = Field(default=3, ge=0, le=10)
    agent_free_plan_enabled: bool = True
    agent_free_plan_require_approval: bool = True
    agent_max_plan_revisions: int = Field(default=5, ge=1, le=20)

    @field_validator("*", mode="before")
    @classmethod
    def reject_boolean_numbers(cls, value, info):
        if isinstance(value, bool) and cls.__pydantic_fields__[info.field_name].annotation in (int, float):
            raise ValueError("Boolean is not a numeric setting")
        return value

    @field_validator("model_enable_thinking", "phoenix_endpoint", "phoenix_api_key", mode="before")
    @classmethod
    def empty_optional_value(cls, value):
        return None if value == "" else value

    @field_validator("executor_source_type", "executor_report_source_type", "executor_result_read_mode", mode="before")
    @classmethod
    def normalize_execution_mode(cls, value):
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("demo_artifacts_root")
    @classmethod
    def resolve_demo_root(cls, value):
        return value if value.is_absolute() else PROJECT_ROOT / value

    @field_validator("executor_runtime_profiles", mode="before")
    @classmethod
    def explicit_profiles_are_nonempty(cls, value):
        if not isinstance(value, (tuple, list)) or not value:
            raise ValueError("Runtime profiles must be a nonempty list")
        return value

    @field_validator("analysis_datasets")
    @classmethod
    def validate_datasets(cls, value):
        from service_contracts.datasets import DatasetDeclaration
        if len(value) > 1000 or any(not key.strip() for key in value):
            raise ValueError("Invalid dataset declarations")
        return {key: DatasetDeclaration.model_validate(item).model_dump(exclude_none=True)
                for key, item in value.items()}

    @model_validator(mode="after")
    def validate_policy(self):
        from service_contracts.project_memory import MemoryLimits
        from service_contracts.session_settings import KERNEL_PROFILE
        KERNEL_PROFILE.validate_python(self.executor_runtime_profile)
        profiles = self.executor_runtime_profiles
        if not profiles:
            profiles = (self.executor_runtime_profile,)
            object.__setattr__(self, "executor_runtime_profiles", profiles)
        for profile in profiles:
            KERNEL_PROFILE.validate_python(profile)
        if len(set(profiles)) != len(profiles) or self.executor_runtime_profile not in profiles:
            raise ValueError("Runtime profiles must be distinct and include the default")
        if self.checkpoint_pool_min_size > self.checkpoint_pool_max_size:
            raise ValueError("Checkpoint pool min exceeds max")
        if self.agent_repair_level > self.agent_repair_level_limit:
            raise ValueError("Repair level exceeds service capability")
        if self.agent_session_analysis_max_chars != 0 and not 2048 <= self.agent_session_analysis_max_chars <= 64000:
            raise ValueError("Analysis context must be 0 or 2048..64000 chars")
        MemoryLimits.from_settings(self)
        return self

    @property
    def executor_executions_url(self) -> str:
        return self._executor_url(self.executor_executions_path)

    @property
    def executor_operations_url(self) -> str:
        return self._executor_url(self.executor_operations_path)

    @property
    def executor_execution_url(self) -> str:
        return self._executor_url(self.executor_execution_path)

    @property
    def executor_result_url(self) -> str:
        return self._executor_url(self.executor_result_path)

    @property
    def executor_notebook_url(self) -> str:
        return self._executor_url(self.executor_notebook_path)

    @property
    def executor_finalize_url(self) -> str:
        return self._executor_url(self.executor_finalize_path)

    @property
    def executor_cancel_url(self) -> str:
        return self._executor_url(self.executor_cancel_path)

    @property
    def executor_artifacts_url(self) -> str:
        return self._executor_url(self.executor_artifacts_path)

    def _executor_url(self, path: str) -> str:
        return (
            f"{self.executor_base_url.rstrip('/')}/"
            f"{path.lstrip('/')}"
        )


def load_agent_settings(
    environ: Mapping[str, Any] | None = None,
    *,
    dotenv_path: Path | None = None,
) -> AgentSettings:
    """Use the process snapshot, or parse an explicit isolated mapping for tests."""
    from service_settings import get_settings, load_settings
    if environ is None and dotenv_path is None:
        return get_settings().agent
    return load_settings(config={}, environ=environ or {}, dotenv_path=dotenv_path).agent


__all__ = [
    "AgentSettings",
    "DEFAULT_EXECUTOR_SHARED_INPUT_ROOT",
    "MOCK_DATA_PATH_BY_TYPE",
    "PROJECT_ROOT",
    "TEST_DATA_SELECTION",
    "build_langgraph_thread_id",
    "load_agent_settings",
    "resolve_mock_data_path",
]
