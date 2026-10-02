"""Agent settings values and compatibility adapter for the central snapshot.

Source loading belongs to service_settings. Explicit mappings support isolated
graph tests without reading the process environment or local files.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

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
    # Maximum combined plan candidates; never generate filler alternatives.
    max_plan_candidates: int = 5
    # Metadata tool rounds per conversation call; synthesis follows this limit.
    agent_discovery_max_rounds: int = 4
    # Only same-session conversation is supplied; oldest messages are trimmed.
    agent_history_message_limit: int = 40
    # Latest terminal analysis supplied to follow-up model calls, measured as serialized JSON chars.
    # 0 disables it; full reports/results remain in Run/Executor records, independent of this excerpt.
    agent_session_analysis_max_chars: int = 16000
    # off=no read/write; manual=read plus explicit memory API; auto_context=extract current user background/preferences.
    # Session data/results are never auto-shared, and deletion cannot be automatically reversed.
    agent_project_memory_mode: str = "manual"
    # Storage guards: all topics (including tombstones), serialized JSON chars,
    # per-topic content chars and atomic batch size. No automatic eviction.
    agent_project_memory_max_topics: int = 64
    agent_project_memory_max_chars: int = 16000
    agent_project_memory_topic_max_chars: int = 1000
    agent_project_memory_max_updates: int = 4
    # Complete memory reference message budget, separate from durable storage.
    # UTF-8 bytes conservatively estimate tokens; 0 in either disables injection.
    agent_project_memory_prompt_max_chars: int = 6000
    agent_project_memory_prompt_max_tokens: int = 4096
    # Trusted dataset IDs → Jupyter paths. Never populated from a request body.
    analysis_datasets: dict = field(default_factory=dict, repr=False, compare=False)
    # Text evidence supplied to the text-only model per Step; full output remains on Executor PV.
    agent_observation_max_chars: int = 16000
    # Safety bound for a MULTI plan; waiting never occupies an Agent execution slot.
    agent_max_operations: int = 64
    # Default repair authorization only when Workflow has no explicit repair policy.
    # 0=off, 1=bindings, 2=failed Tool implementation, 3=registered replan + HITL, 4=execution-local code.
    agent_repair_level: int = 0
    # Service capability ceiling; HITL cannot grant a level above this limit.
    agent_repair_level_limit: int = 4
    # Run-wide accepted correction Operation budget; also default when level > 0.
    agent_max_repair_attempts: int = 3
    # Allow execution-local code only after the user requests a plan revision.
    agent_free_plan_enabled: bool = True
    # False allows ONE complete free-code proposal to execute after notifying the user.
    # Multiple candidates, unanswered questions and missing required inputs still require HITL.
    agent_free_plan_require_approval: bool = True
    # User revision/clarification turns per Run, independent of model retries/repair attempts.
    agent_max_plan_revisions: int = 5

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

    import json
    from service_contracts.datasets import DatasetDeclaration
    max_candidates = int(env.get('MAX_PLAN_CANDIDATES', '5'))
    discovery_rounds = int(env.get('AGENT_DISCOVERY_MAX_ROUNDS', '4'))
    history_limit = int(env.get('AGENT_HISTORY_MESSAGE_LIMIT', '40'))
    session_analysis_limit = int(env.get('AGENT_SESSION_ANALYSIS_MAX_CHARS', '16000'))
    if session_analysis_limit != 0 and not 2048 <= session_analysis_limit <= 64000:
        raise ValueError('AGENT_SESSION_ANALYSIS_MAX_CHARS must be 0 or 2048..64000')
    memory_mode = env.get('AGENT_PROJECT_MEMORY_MODE', 'manual')
    if memory_mode not in {'off','manual','auto_context'}:
        raise ValueError('AGENT_PROJECT_MEMORY_MODE must be off, manual or auto_context')
    from service_contracts.project_memory import MemoryLimits
    memory_limits = MemoryLimits(**{name: int(env.get('AGENT_PROJECT_MEMORY_' + name.upper(), str(default)))
        for name, default in vars(MemoryLimits()).items()})
    observation_limit = int(env.get('AGENT_OBSERVATION_MAX_CHARS', '16000'))
    max_operations = int(env.get('AGENT_MAX_OPERATIONS', '64'))
    plan_revisions = int(env.get('AGENT_MAX_PLAN_REVISIONS', '5'))
    if not 1 <= plan_revisions <= 20:
        raise ValueError('AGENT_MAX_PLAN_REVISIONS must be 1..20')
    repair_level = int(env.get('AGENT_REPAIR_LEVEL', '0'))
    repair_limit = int(env.get('AGENT_REPAIR_LEVEL_LIMIT', '4'))
    repair_attempts = int(env.get('AGENT_MAX_REPAIR_ATTEMPTS', '3'))
    if not 0 <= repair_level <= repair_limit <= 4 or not 0 <= repair_attempts <= 10:
        raise ValueError('Invalid Agent repair level/attempt limits')
    if not 1024 <= observation_limit <= 64000 or not 1 <= max_operations <= 256:
        raise ValueError('Invalid Agent observation/operation limits')
    if not 1 <= max_candidates <= 20 or not 2 <= history_limit <= 200 or not 1 <= discovery_rounds <= 12:
        raise ValueError('Invalid planning candidate/history limits')
    datasets = json.loads(env.get('ANALYSIS_DATASETS', '{}'))
    if not isinstance(datasets, dict) or len(datasets) > 1000 or any(not isinstance(key, str) or not key.strip() for key in datasets):
        raise ValueError('Invalid ANALYSIS_DATASETS mapping')
    datasets = {key: DatasetDeclaration.model_validate(value).model_dump(exclude_none=True)
                for key, value in datasets.items()}
    return AgentSettings(
        max_plan_candidates=max_candidates, agent_history_message_limit=history_limit,
        agent_session_analysis_max_chars=session_analysis_limit, agent_project_memory_mode=memory_mode,
        **{'agent_project_memory_' + name: value for name, value in vars(memory_limits).items()},
        agent_discovery_max_rounds=discovery_rounds,
        analysis_datasets=datasets,
        agent_observation_max_chars=observation_limit, agent_max_operations=max_operations,
        agent_repair_level=repair_level,agent_repair_level_limit=repair_limit,agent_max_repair_attempts=repair_attempts,
        agent_free_plan_enabled=_as_bool(env.get('AGENT_FREE_PLAN_ENABLED'),True),
        agent_free_plan_require_approval=_as_bool(env.get('AGENT_FREE_PLAN_REQUIRE_APPROVAL'),True),
        agent_max_plan_revisions=plan_revisions,
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
    "MOCK_DATA_PATH_BY_TYPE",
    "PROJECT_ROOT",
    "TEST_DATA_SELECTION",
    "build_langgraph_thread_id",
    "load_agent_settings",
    "resolve_mock_data_path",
]
