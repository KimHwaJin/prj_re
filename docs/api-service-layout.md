# API 서비스 구조와 정리 내역

2026-10-06, 101 정리 기준. 기존 패키지는 파일을 복제하거나 재export하는 호환 모듈 없이 이동·삭제했다. HTTP API의 경로·로그인·Run/HITL/SSE 흐름은 유지한다.

## 현재 구조

```text
src/api_service/
  api/                    FastAPI dependency·pagination·problem response·v1 routes
  infrastructure/         SQLAlchemy DB·LangGraph Store·Executor binding 자원
  models/                 현재 SQLAlchemy 모델과 enum·base
  schemas/                현재 REST 요청·응답 모델
  repositories/           사용자·프로젝트·세션·메시지 기본 조회
  resources/              CRUD·SSO 사용자·소유권·삭제 정책·프로젝트 메모리
  runs/                   Run 접수·실행·조회·로그·SSE·Task
    commands/             단일 명령 원장 admission·claim·outcome·wakeup
    protocols/            시작·사용자 승인·Executor 입력 실행 정책
    persistence/          그래프 결과·메시지·이벤트·계획 영속화
  workers/                실행 Worker·재조정 작업
    executor_events/      외부 Streams 수신·Inbox·순서·binding·메트릭
  workflows/              Workflow CRUD·JSON 파일 저장
    search/               embedding·indexing·retrieval
  web/                    /demo 라우터와 HTML
  utils.py                현재 API 공용 값 변환·시간·경량 helper

tests/api_service/         API 회귀·DB 이행·Worker 테스트(배포 패키지 제외)
src/service_runtime/
  observability/          공통 Phoenix 초기화
```

## 소유권과 경계

- `api/v1/routes`는 HTTP 입력·dependency·상태 코드·response 조립을 담당한다.
- `resources`는 사용자/프로젝트/세션/메시지 CRUD와 SSO 최초 등록·기본 프로젝트·소유권 정책을 담당한다. 프로젝트 소유자는 `projects.user_id` 하나다.
- `runs/runtime.py`만 Agent 구현을 import하고, 프로세스 수명의 graph·checkpointer·binding 자원을 조립한다. `runs/ownership.py`는 현재 `acquire`, `run_owned` 점유 처리만 제공한다.
- `workers/agent.py`가 사용자 요청과 Executor 결과를 단일 원장·공통 한도로 실행한다. `workers/executor_events`는 수신·영속화·순서 보장만 담당한다.
- `workflows`는 실제 현재 Workflow CRUD와 pgvector 추천을 유지한다. 이전 PostgresWorkflowStore는 현재 그래프에서 연결되지 않아 삭제했다.
- DB/Store/모델 호출 설정을 API Settings에 중복 선언하지 않는다. LLM·checkpoint·Executor 실행 설정은 AgentSettings 한 곳에서 정의하고 중앙 snapshot으로 주입한다. API의 `llm_token_*`는 모델 설정이 아니라 SSE 버퍼 정책이므로 유지한다.

## 삭제한 코드

| 삭제 대상 | 근거 |
|---|---|
| 구 `llm_service.py`, LLMRun 모델·enum·관계 | Run 기반 Agent 경로에서 호출되지 않는 직접 LLM 구현 |
| 구 `workflow_persistence.py`, `service_contracts/workflow.py` | 현재 runtime에 주입되지 않는 다른 Workflow store와 전용 계약 |
| 구 `agent_run_repository.py`, agent/LLM 전용 request schema·빈 schema | 폐기 Redis 전달 방식, 실제 현재 라우터 미사용 |
| ProjectMember 모델·관계·owner 중복 insert | 공유/멤버십 요구 없음; 현재 owner 검사에 사용하지 않음 |
| `runs/commands/migrate.py` | 일회 이관을 실제 Alembic revision으로 이동; 상시 runtime CLI 불필요 |
| `get_api_worker_bridge` 전역 singleton·종료 wrapper | 실제 runtime이 소유하는 bridge와 중복; 실제 bridge class는 유지 |
| 합성 이벤트 실행 `run_event_owned` | production 호출 없음; fault injection 테스트의 harness로 이동 |
| 미사용 repository 메서드·설정 복사·worker/config 재export | 현재 호출 또는 별도 책임 없음 |

구 `core`, `services`, `worker`, `agent_worker`, `observability`, `models/common`, `schemas/common`, `static`, `test` 디렉토리는 제거한다. __init__.py star reexport도 없앴다. 런타임 파일 수 감소와 테스트 이동은 구분한다: 테스트는 버리지 않고 tests로 옮긴 것이다.

## DB 삭제

`llm_runs`, `project_members`, `jupyter_servers`, `ew_commands`, `ew_outbox`, `ew_audit`, 구 `workflow_catalog`, `workflow_executions`, `workflow_adaptive_history` **9개 테이블**, view 1개, enum 2개, AgentRun 구 전달 필드 10개를 제거한다. 기존 대기 명령만 현재 원장으로 이관하며 폐기 테이블을 감사용으로 남기지 않는다. [DDL·적용 순서](database_migrations.md)를 따른다.

LangGraph checkpoint·Store·현재 Run/Task/로그/Workflow·Inbox/binding은 실제 서비스에서 사용하므로 유지한다. 과거 Alembic revision과 측정 보고서는 기존 DB upgrade·비교 결과 재현에 필요한 이력이다. 현재 실행 경로로 재사용하지 않는다.

## 파일 이동 추적

아래 경로는 모두 `src/api_service/` 기준이다. 실제 테스트 파일은 `src/api_service/test/`에서 `tests/api_service/`로 옮겼다. Phoenix는 `src/api_service/observability/`에서 `src/service_runtime/observability/`로 옮겼다.

| 이전 | 현재 |
|---|---|
| `core/auth.py` | `api/dependencies.py` |
| `core/database.py` | `infrastructure/database.py` |
| `core/memory_store.py` | `infrastructure/memory_store.py` |
| `core/enums.py` | `models/enums.py` |
| `core/pagination.py` | `api/pagination.py` |
| `core/problems.py` | `api/problems.py` |
| `core/user_identity.py` | `resources/identity.py` |
| `core/execution_claim.py` | `runs/claim_context.py` |
| `core/execution_lifecycle.py` | `runs/lifecycle.py` |
| `agent_run_worker.py` | `workers/agent.py` |
| `task_lock_reconciler.py` | `workers/reconciler.py` |
| `agent_worker/worker_main.py` | `workers/executor_events/main.py` |
| `agent_worker/worker_hooks.py` | `workers/executor_events/event_types.py` |
| `agent_worker/api_bridge.py` | `infrastructure/executor_bindings.py` |
| `web_console.py` | `web/console.py` |
| `static/demo.html` | `web/static/demo.html` |
| `services/user_service.py` | `resources/users.py` |
| `services/user_queries.py` | `resources/user_queries.py` |
| `services/sso_user_service.py` | `resources/sso_users.py` |
| `services/project_service.py` | `resources/projects.py` |
| `services/project_queries.py` | `resources/project_queries.py` |
| `services/project_memory_policy.py` | `resources/project_memory.py` |
| `services/session_service.py` | `resources/sessions.py` |
| `services/session_activity.py` | `resources/session_activity.py` |
| `services/message_service.py` | `resources/messages.py` |
| `services/resource_lifecycle.py` | `resources/lifecycle.py` |
| `services/cascade_service.py` | `resources/cascade.py` |
| `services/agent_graph_service.py` | `runs/runtime.py` |
| `services/agent_project_context.py` | `runs/project_context.py` |
| `services/agent_run_log_service.py` | `runs/logs.py` |
| `services/llm_token_event_service.py` | `runs/token_events.py` |
| `services/public_run_service.py` | `runs/service.py` |
| `services/run_diagnostics.py` | `runs/diagnostics.py` |
| `services/run_log_query.py` | `runs/log_queries.py` |
| `services/run_stream_service.py` | `runs/streaming.py` |
| `services/session_execution.py` | `runs/ownership.py` |
| `services/task_event_service.py` | `runs/task_events.py` |
| `services/task_service.py` | `runs/tasks.py` |
| `services/chat_crud_message_sink.py` | `runs/persistence/messages.py` |
| `services/graph_crud_persistence.py` | `runs/persistence/graph.py` |
| `services/graph_event_persistence.py` | `runs/persistence/events.py` |
| `services/graph_recovery.py` | `runs/persistence/recovery.py` |
| `services/graph_result_batch.py` | `runs/persistence/batch.py` |
| `services/plan_event_persistence.py` | `runs/persistence/plans.py` |
| `services/helpers.py` | `utils.py` |
| `services/workflow_service.py` | `workflows/service.py` |
| `services/workflow_file_store.py` | `workflows/file_store.py` |
| `models/common/agent_command_model.py` | `models/agent_command_model.py` |
| `models/common/agent_run_log_model.py` | `models/agent_run_log_model.py` |
| `models/common/message_model.py` | `models/message_model.py` |
| `models/common/session_execution_model.py` | `models/session_execution_model.py` |
| `models/common/user_model.py` | `models/user_model.py` |
| `models/common/project_model.py` | `models/project_model.py` |
| `models/common/session_model.py` | `models/session_model.py` |
| `models/common/agent_run_model.py` | `models/agent_run_model.py` |
| `models/common/task_model.py` | `models/task_model.py` |
| `models/common/llm_run_model.py` | 삭제 |
| `models/common/task_event_model.py` | `models/task_event_model.py` |
| `models/common/workflow_model.py` | `models/workflow_model.py` |
| `schemas/common/run_schema.py` | `schemas/run_schema.py` |
| `schemas/common/llm_schema.py` | 삭제 |
| `schemas/common/session_activity_schema.py` | `schemas/session_activity_schema.py` |
| `schemas/common/user_schema.py` | `schemas/user_schema.py` |
| `schemas/common/workflow_schema.py` | `schemas/workflow_schema.py` |
| `schemas/common/api_schema.py` | `schemas/api_schema.py` |
| `schemas/common/analysis_request_schema.py` | 삭제 |
| `schemas/common/project_memory_schema.py` | `schemas/project_memory_schema.py` |
| `schemas/common/project_schema.py` | `schemas/project_schema.py` |
| `schemas/common/agent_schema.py` | 삭제 |
| `schemas/common/session_schema.py` | `schemas/session_schema.py` |
| `schemas/common/message_schema.py` | `schemas/message_schema.py` |
| `schemas/common/run_diagnostics_schema.py` | `schemas/run_diagnostics_schema.py` |
| `worker/wakeup.py` | `workers/executor_events/wakeup.py` |
| `worker/store.py` | `workers/executor_events/store.py` |
| `worker/config.py` | 삭제 |
| `worker/ingress.py` | `workers/executor_events/ingress.py` |
| `worker/telemetry.py` | `workers/executor_events/telemetry.py` |
| `worker/runtime.py` | `workers/executor_events/runtime.py` |
| `worker/consumer.py` | `workers/executor_events/consumer.py` |
| `worker/redis_streams.py` | `workers/executor_events/redis_streams.py` |
| `workflows/embedding.py` | `workflows/search/embedding.py` |
| `workflows/runtime.py` | `workflows/search/runtime.py` |
| `workflows/retrieval.py` | `workflows/search/retrieval.py` |
| `workflows/indexing.py` | `workflows/search/indexing.py` |
