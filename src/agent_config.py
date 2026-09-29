"""Agent settings values and compatibility adapter for the central snapshot.

Source loading belongs to service_settings. Explicit mappings support isolated
graph tests without reading the process environment or local files.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

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


# Local/manual HITL test shortcut. Production frontends should resume the
# interrupt with a DataSelectionResponse-shaped JSON object instead.
TEST_DATA_SELECTION_TRIGGER = "mock"
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

LOCAL_MOCK_USER_ID = "mock-user-001"
LOCAL_MOCK_PROJECT_ID = "mock-project-001"
LOCAL_MOCK_SESSION_ID = "mock-session-001"

# 현재 서비스에서 활성화한 분석 의도입니다. 향후 지원 범위를 넓힐 때
# root_cause 또는 data_drift를 이 tuple에 추가하면 됩니다.
ENABLED_ANALYSIS_INTENTS = ("failure_prediction","root_cause", "data_drift")


def build_langgraph_thread_id(session_id: str) -> str:
    """Build the checkpoint thread key for one conversation session."""
    normalized_session_id = session_id.strip()
    if not normalized_session_id:
        raise ValueError("session_id is required")
    return normalized_session_id


def build_local_mock_request_context(
    *,
    session_id: str = LOCAL_MOCK_SESSION_ID,
) -> dict[str, str]:
    """Return request-scoped IDs for local/manual graph tests only."""
    return {
        "user_id": LOCAL_MOCK_USER_ID,
        "project_id": LOCAL_MOCK_PROJECT_ID,
        "session_id": session_id,
        "request_id": f"mock-request-{uuid4()}",
        "thread_id": build_langgraph_thread_id(session_id),
    }


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError("Invalid boolean setting")


def _as_optional_bool(value: str | None) -> bool | None:
    """Parse an optional boolean whose absence means do not send an option."""
    if value is None or not value.strip():
        return None
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {value!r}")


@dataclass(frozen=True)
class AgentSettings:
    environment: str
    model_provider: str
    model_name: str
    model_api_key: str | None
    api_base_url: str | None
    model_temperature: float
    model_timeout_seconds: float
    model_max_retries: int
    model_enable_thinking: bool | None
    checkpoint_db_uri: str
    checkpoint_setup_on_start: bool
    checkpoint_pool_min_size: int
    checkpoint_pool_max_size: int
    checkpoint_pool_timeout_seconds: float
    strict_checkpoint_msgpack: bool
    executor_base_url: str
    executor_tls_verify: bool
    executor_runtime_profile: str
    executor_executions_path: str
    executor_operations_path: str
    executor_execution_path: str
    executor_result_path: str
    executor_notebook_path: str
    executor_finalize_path: str
    executor_cancel_path: str
    executor_artifacts_path: str
    executor_shared_input_root: Path
    executor_result_read_mode: str
    executor_shared_result_root: Path
    data_mock: bool
    executor_source_type: str
    executor_report_source_type: str
    executor_report_append_to_notebook: bool
    executor_timeout_seconds: float
    executor_operation_timeout_seconds: int
    executor_operation_wait_timeout_seconds: int
    executor_submit_enabled: bool
    demo_artifacts_enabled: bool
    demo_artifacts_root: Path
    phoenix_endpoint: str | None
    phoenix_project_name: str
    phoenix_api_key: str | None
    max_workflow_revisions: int
    workflow_recommendation_enabled: bool
    workflow_similarity_score: float
    model_structured_output_mode: str
    executor_http_max_connections: int = 8
    executor_http_connect_timeout_seconds: float = 5
    executor_http_pool_timeout_seconds: float = 5
    executor_http_max_response_bytes: int = 16 * 1024 * 1024
    model_mock_delay_ms: int = 0
    model_catalog: Any = field(default=None, repr=False, compare=False)

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
    environ: Mapping[str, str] | None = None,
    *,
    dotenv_path: Path | None = None,
) -> AgentSettings:
    """Use the process snapshot, or parse an explicit isolated mapping for tests."""
    if environ is None and dotenv_path is None:
        from service_settings import get_settings
        return get_settings().agent
    if dotenv_path is not None:
        from service_settings import read_local_env
        env = read_local_env(dotenv_path)
        env.update(environ or {})
    else:
        env = dict(environ)
    return _agent_settings_from_mapping(env)


def _agent_settings_from_mapping(env: Mapping[str, Any]) -> AgentSettings:
    """Pure legacy value adapter; never reads environment, dotenv or YAML."""

    for key, default in {
        "EXECUTOR_HTTP_MAX_CONNECTIONS": 8,
        "EXECUTOR_HTTP_CONNECT_TIMEOUT_SECONDS": 5,
        "EXECUTOR_HTTP_POOL_TIMEOUT_SECONDS": 5,
        "EXECUTOR_HTTP_MAX_RESPONSE_BYTES": 16 * 1024 * 1024,
    }.items():
        value = float(env.get(key, default))
        if not 0 < value < float("inf"):
            raise ValueError("Invalid Executor HTTP limit")

    mock_delay_ms = int(env.get("MODEL_MOCK_DELAY_MS", "0"))
    if not 0 <= mock_delay_ms <= 60000:
        raise ValueError("MODEL_MOCK_DELAY_MS must be between 0 and 60000")

    pool_min = int(env.get("CHECKPOINT_POOL_MIN_SIZE", "1"))
    pool_max = int(env.get("CHECKPOINT_POOL_MAX_SIZE", "4"))
    pool_timeout = float(env.get("CHECKPOINT_POOL_TIMEOUT_SECONDS", "10"))
    if not 1 <= pool_min <= pool_max:
        raise ValueError("Checkpoint pool requires 1 <= min_size <= max_size")
    if not 0 < pool_timeout < float("inf"):
        raise ValueError("Checkpoint pool timeout must be finite and positive")

    phoenix_endpoint = env.get("PHOENIX_ENDPOINT")
    phoenix_api_key = env.get("PHOENIX_API_KEY")
    provider = env.get("MODEL_PROVIDER") or "openai_compatible"

    demo_artifacts_root = Path(
        env.get("DEMO_ARTIFACTS_ROOT", str(PROJECT_ROOT / "demo_artifacts"))
    )
    if not demo_artifacts_root.is_absolute():
        demo_artifacts_root = PROJECT_ROOT / demo_artifacts_root
    model_structured_output_mode = env.get(
        "MODEL_STRUCTURED_OUTPUT_MODE", "prompt_json"
    ).strip()
    if model_structured_output_mode not in {
        "prompt_json",
        "provider_json_schema",
    }:
        raise ValueError(
            "MODEL_STRUCTURED_OUTPUT_MODE must be 'prompt_json' or "
            "'provider_json_schema'"
        )

    executor_source_type = env.get("EXECUTOR_SOURCE_TYPE", "PATH").strip().upper()
    if executor_source_type not in {"PATH", "INLINE"}:
        raise ValueError("EXECUTOR_SOURCE_TYPE must be 'PATH' or 'INLINE'")
    executor_report_source_type = env.get(
        "EXECUTOR_REPORT_SOURCE_TYPE", "INLINE"
    ).strip().upper()
    if executor_report_source_type not in {"PATH", "INLINE"}:
        raise ValueError(
            "EXECUTOR_REPORT_SOURCE_TYPE must be 'PATH' or 'INLINE'"
        )
    executor_result_read_mode = env.get(
        "EXECUTOR_RESULT_READ_MODE", "API"
    ).strip().upper()
    if executor_result_read_mode not in {"API", "MANIFEST"}:
        raise ValueError(
            "EXECUTOR_RESULT_READ_MODE must be 'API' or 'MANIFEST'"
        )

    executor_operation_timeout_seconds = int(
        env.get("EXECUTOR_OPERATION_TIMEOUT_SECONDS", "600")
    )
    executor_operation_wait_timeout_seconds = int(
        env.get("EXECUTOR_OPERATION_WAIT_TIMEOUT_SECONDS", "600")
    )
    if executor_operation_timeout_seconds <= 0:
        raise ValueError("EXECUTOR_OPERATION_TIMEOUT_SECONDS must be greater than 0")
    if executor_operation_wait_timeout_seconds < 30:
        raise ValueError(
            "EXECUTOR_OPERATION_WAIT_TIMEOUT_SECONDS must be at least 30"
        )

    return AgentSettings(
        model_mock_delay_ms=mock_delay_ms,
        environment=env.get("APP_ENV", "development"),
        model_provider=provider,
        model_name=env.get("MODEL_NAME"),
        model_api_key=env.get("MODEL_API_KEY"),
        api_base_url=env.get("API_BASE_URL"),
        model_temperature=float(env.get("MODEL_TEMPERATURE", "0")),
        model_timeout_seconds=float(env.get("MODEL_TIMEOUT_SECONDS", "60")),
        model_max_retries=int(env.get("MODEL_MAX_RETRIES", "2")),
        model_enable_thinking=_as_optional_bool(
            env.get("MODEL_ENABLE_THINKING")
        ),
        checkpoint_db_uri=env.get(
            "CHECKPOINT_DB_URI",
            "postgresql://postgres:postgres@localhost:5432/dtest_agent?sslmode=disable",
        ),
        checkpoint_setup_on_start=_as_bool(
            env.get("CHECKPOINT_SETUP_ON_START"), True
        ),
        checkpoint_pool_min_size=pool_min,
        checkpoint_pool_max_size=pool_max,
        checkpoint_pool_timeout_seconds=pool_timeout,
        strict_checkpoint_msgpack=_as_bool(
            env.get("LANGGRAPH_STRICT_MSGPACK"), True
        ),
        executor_base_url=env.get("EXECUTOR_BASE_URL", "http://executor:8080"),
        executor_tls_verify=_as_bool(env.get("EXECUTOR_TLS_VERIFY"), True),
        executor_runtime_profile=(
            env.get("EXECUTOR_RUNTIME_PROFILE", "ml").strip()
        ),
        executor_executions_path=env.get(
            "EXECUTOR_EXECUTIONS_PATH",
            env.get("EXECUTOR_JOBS_PATH", "/api/v1/executions"),
        ),
        executor_operations_path=env.get(
            "EXECUTOR_OPERATIONS_PATH",
            "/api/v1/executions/{execution_id}/operations",
        ),
        executor_execution_path=env.get(
            "EXECUTOR_EXECUTION_PATH",
            "/api/v1/executions/{execution_id}",
        ),
        executor_result_path=env.get(
            "EXECUTOR_RESULT_PATH",
            "/api/v1/executions/{execution_id}/result",
        ),
        executor_notebook_path=env.get(
            "EXECUTOR_NOTEBOOK_PATH",
            "/api/v1/executions/{execution_id}/notebook",
        ),
        executor_finalize_path=env.get(
            "EXECUTOR_FINALIZE_PATH",
            "/api/v1/executions/{execution_id}/finalize",
        ),
        executor_cancel_path=env.get(
            "EXECUTOR_CANCEL_PATH",
            "/api/v1/executions/{execution_id}/cancel",
        ),
        executor_artifacts_path=env.get(
            "EXECUTOR_ARTIFACTS_PATH",
            "/api/v1/executions/{execution_id}/artifacts",
        ),
        executor_shared_input_root=Path(
            env.get(
                "EXECUTOR_SHARED_INPUT_ROOT",
                str(DEFAULT_EXECUTOR_SHARED_INPUT_ROOT),
            )
        ),
        executor_result_read_mode=executor_result_read_mode,
        executor_shared_result_root=Path(
            env.get("EXECUTOR_SHARED_RESULT_ROOT", "/workspace/pv")
        ),
        data_mock=_as_bool(env.get("DATA_MOCK"), False),
        executor_source_type=executor_source_type,
        executor_report_source_type=executor_report_source_type,
        executor_report_append_to_notebook=_as_bool(
            env.get("EXECUTOR_REPORT_APPEND_TO_NOTEBOOK"), True
        ),
        executor_http_max_connections=int(env.get("EXECUTOR_HTTP_MAX_CONNECTIONS", 8)),
        executor_http_connect_timeout_seconds=float(env.get("EXECUTOR_HTTP_CONNECT_TIMEOUT_SECONDS", 5)),
        executor_http_pool_timeout_seconds=float(env.get("EXECUTOR_HTTP_POOL_TIMEOUT_SECONDS", 5)),
        executor_http_max_response_bytes=int(env.get("EXECUTOR_HTTP_MAX_RESPONSE_BYTES", 16 * 1024 * 1024)),
        executor_timeout_seconds=float(env.get("EXECUTOR_TIMEOUT_SECONDS", "30")),
        executor_operation_timeout_seconds=executor_operation_timeout_seconds,
        executor_operation_wait_timeout_seconds=(
            executor_operation_wait_timeout_seconds
        ),
        executor_submit_enabled=_as_bool(
            env.get("EXECUTOR_SUBMIT_ENABLED"), True
        ),
        demo_artifacts_enabled=_as_bool(
            env.get("DEMO_ARTIFACTS_ENABLED"), True
        ),
        demo_artifacts_root=demo_artifacts_root,
        phoenix_endpoint=phoenix_endpoint,
        phoenix_project_name=env.get("PHOENIX_PROJECT_NAME", "dtest-agent"),
        phoenix_api_key=phoenix_api_key,
        max_workflow_revisions=int(env.get("MAX_WORKFLOW_REVISIONS", "10")),
        workflow_recommendation_enabled=_as_bool(
            env.get("WORKFLOW_RECOMMENDATION_ENABLED"), True
        ),
        workflow_similarity_score=float(
            env.get("WORKFLOW_SIMILARITY_SCORE", "0.93")
        ),
        model_structured_output_mode=model_structured_output_mode,
    )


__all__ = [
    "AgentSettings",
    "DEFAULT_EXECUTOR_SHARED_INPUT_ROOT",
    "ENABLED_ANALYSIS_INTENTS",
    "LOCAL_MOCK_PROJECT_ID",
    "LOCAL_MOCK_SESSION_ID",
    "LOCAL_MOCK_USER_ID",
    "MOCK_DATA_PATH_BY_TYPE",
    "PROJECT_ROOT",
    "TEST_DATA_SELECTION",
    "TEST_DATA_SELECTION_TRIGGER",
    "build_langgraph_thread_id",
    "build_local_mock_request_context",
    "load_agent_settings",
    "resolve_mock_data_path",
]
