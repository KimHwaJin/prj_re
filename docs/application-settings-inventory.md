# 템플릿 설정 재사용·서비스 추가·제거 목록

2026-10-06 / 100. 중앙 loader의 별칭을 제외한 소비 설정은 174개다. 기존40 메시지 제한은 제거하고 템플릿의 턴 정책으로 대체했다. 모든 실행에서 환경 파일 하나를 읽는다. 아래 그룹은 설명용이며 YAML 계층이 아니다.

## 템플릿에서 재사용: 10개

| 외부 YAML 키 | 내부 연결 | 변경 |
|---|---|---|
| PORT | API server_port | SERVER_PORT 중복 선언 제거 |
| PRIVATE_LLM_MODEL_NAME | API/Agent 기본 모델 | MODEL_NAME·LLM_MODEL_NAME은 호환 별칭 |
| PRIVATE_LLM_ENDPOINT | API/Agent base URL | API_BASE_URL·LLM_API_BASE_URL은 호환 별칭; /v1 그대로 |
| PRIVATE_LLM_API_KEY | API/Agent 기본 model key | MODEL_API_KEY·LLM_API_KEY는 호환 별칭 |
| RECURSION_LIMIT | 외부/내부 graph config | 내부 고정32 제거; 각 호출에 동일 설정 주입 |
| ACTIVE_MULTI_TURN | 모델 입력 이력 | false여도 현재 Run·HITL·checkpoint 보존 |
| SET_MAX_HISTORY | 이전 N 요청 턴 + 현재 Run | 메시지 단위 폐기, 기본6 |
| ACTIVE_TRACE | standalone tracing | 플랫폼 tracing 중복 초기화/종료 제거 |
| PHOENIX_ENDPOINT | standalone collector | 플랫폼은 자체 초기화 사용 |
| PHOENIX_API_KEY | standalone exporter 인증 | 플랫폼은 자체 초기화 사용 |

## 서비스 전용 추가 설정

[config.service.example.yml](../config.service.example.yml)의 **147개 명시 항목**만 기존 템플릿 환경 파일에 추가하면 예제의 정책·연결 설정을 모두 포함한다. 모두 필수라는 뜻은 아니며 pool·대기·검색 예산 등은 코드 기본값이 있다. DB/Redis/Executor, 기본 kernel/공유 경로, SSO origin·adapter를 실제 환경에 맞춘다. 임베딩 주소·모델·차원은 추천 사용 시 별도 추가한다. 실제 비밀값은 이 문서에 기록하지 않는다.

| 설정 | 분류 | 소비 영역 |
|---|---|---|
| AGENT_DISCOVERY_MAX_ROUNDS | 서비스 추가(예제 명시) | 계획·HITL·분석 맥락·프로젝트 메모리 |
| AGENT_FREE_PLAN_ENABLED | 서비스 추가(예제 명시) | 계획·HITL·분석 맥락·프로젝트 메모리 |
| AGENT_FREE_PLAN_REQUIRE_APPROVAL | 서비스 추가(예제 명시) | 계획·HITL·분석 맥락·프로젝트 메모리 |
| AGENT_MAX_OPERATIONS | 서비스 추가(예제 명시) | 계획·HITL·분석 맥락·프로젝트 메모리 |
| AGENT_MAX_PLAN_REVISIONS | 서비스 추가(예제 명시) | 계획·HITL·분석 맥락·프로젝트 메모리 |
| AGENT_MAX_REPAIR_ATTEMPTS | 서비스 추가(예제 명시) | 계획·HITL·분석 맥락·프로젝트 메모리 |
| AGENT_OBSERVATION_MAX_CHARS | 서비스 추가(예제 명시) | 계획·HITL·분석 맥락·프로젝트 메모리 |
| AGENT_PROJECT_MEMORY_MAX_CHARS | 서비스 추가(예제 명시) | 계획·HITL·분석 맥락·프로젝트 메모리 |
| AGENT_PROJECT_MEMORY_MAX_UPDATES | 서비스 추가(예제 명시) | 계획·HITL·분석 맥락·프로젝트 메모리 |
| AGENT_PROJECT_MEMORY_MODE | 서비스 추가(예제 명시) | 계획·HITL·분석 맥락·프로젝트 메모리 |
| AGENT_PROJECT_MEMORY_PATCH_MAX_CHARS | 서비스 추가(예제 명시) | 계획·HITL·분석 맥락·프로젝트 메모리 |
| AGENT_PROJECT_MEMORY_PROMPT_MAX_CHARS | 서비스 추가(예제 명시) | 계획·HITL·분석 맥락·프로젝트 메모리 |
| AGENT_PROJECT_MEMORY_PROMPT_MAX_TOKENS | 서비스 추가(예제 명시) | 계획·HITL·분석 맥락·프로젝트 메모리 |
| AGENT_REPAIR_LEVEL | 서비스 추가(예제 명시) | 계획·HITL·분석 맥락·프로젝트 메모리 |
| AGENT_REPAIR_LEVEL_LIMIT | 서비스 추가(예제 명시) | 계획·HITL·분석 맥락·프로젝트 메모리 |
| AGENT_SESSION_ANALYSIS_MAX_CHARS | 서비스 추가(예제 명시) | 계획·HITL·분석 맥락·프로젝트 메모리 |
| AGENT_WORKER_CONCURRENCY | 서비스 추가(예제 명시) | app.py·Worker·SSE·종료 |
| AGENT_WORKER_ENABLED | 서비스 추가(예제 명시) | app.py·Worker·SSE·종료 |
| AGENT_WORKER_MAX_RETRIES | 서비스 추가(예제 명시) | app.py·Worker·SSE·종료 |
| AGENT_WORKER_NOTIFY_ENABLED | 서비스 추가(예제 명시) | app.py·Worker·SSE·종료 |
| AGENT_WORKER_POLL_INTERVAL_SECONDS | 서비스 추가(예제 명시) | app.py·Worker·SSE·종료 |
| AGENT_WORKER_RECONCILE_INTERVAL_SECONDS | 서비스 추가(예제 명시) | app.py·Worker·SSE·종료 |
| AGENT_WORKER_RETRY_BACKOFF_SECONDS | 서비스 추가(예제 명시) | app.py·Worker·SSE·종료 |
| AGENT_WORKER_RETRY_MAX_BACKOFF_SECONDS | 서비스 추가(예제 명시) | app.py·Worker·SSE·종료 |
| ANALYSIS_DATASETS | 서비스 추가(예제 명시) | 계획·HITL·분석 맥락·프로젝트 메모리 |
| API_V1_PREFIX | 서비스 추가(예제 명시) | app.py·Worker·SSE·종료 |
| APP_ENV | 환경 선택 입력 | app.py·Worker·SSE·종료 |
| APP_NAME | 서비스 추가(예제 명시) | app.py·Worker·SSE·종료 |
| CHECKPOINT_DB_URI | 서비스 추가(예제 명시) | LangGraph checkpoint runtime |
| CHECKPOINT_POOL_MAX_SIZE | 서비스 추가(예제 명시) | LangGraph checkpoint runtime |
| CHECKPOINT_POOL_MIN_SIZE | 서비스 추가(예제 명시) | LangGraph checkpoint runtime |
| CHECKPOINT_POOL_TIMEOUT_SECONDS | 서비스 추가(예제 명시) | LangGraph checkpoint runtime |
| CHECKPOINT_SETUP_ON_START | 서비스 추가(예제 명시) | LangGraph checkpoint runtime |
| DATABASE_MAX_OVERFLOW | 서비스 추가(예제 명시) | CRUD·명령·Store DB/공용 Redis 접속 |
| DATABASE_POOL_RECYCLE_SECONDS | 서비스 추가(예제 명시) | CRUD·명령·Store DB/공용 Redis 접속 |
| DATABASE_POOL_SIZE | 서비스 추가(예제 명시) | CRUD·명령·Store DB/공용 Redis 접속 |
| DATABASE_POOL_TIMEOUT_SECONDS | 서비스 추가(예제 명시) | CRUD·명령·Store DB/공용 Redis 접속 |
| DATABASE_PREPARED_STATEMENT_CACHE_SIZE | 서비스 추가(예제 명시) | CRUD·명령·Store DB/공용 Redis 접속 |
| DATABASE_URL | 서비스 추가(예제 명시) | CRUD·명령·Store DB/공용 Redis 접속 |
| DEFAULT_MODEL | 선택 입력/파생 | 기본/선택 모델·Agent builder |
| EVENT_WORKER_ENABLED | 서비스 추가(예제 명시) | app.py·Worker·SSE·종료 |
| EW_BATCH_SIZE | 서비스 추가(예제 명시) | Executor Streams·Inbox routing |
| EW_CLAIM_IDLE_MILLISECONDS | 서비스 추가(예제 명시) | Executor Streams·Inbox routing |
| EW_DATABASE_URL | 선택 입력/파생 | Executor Streams·Inbox routing |
| EW_EVENT_GROUP_NAME | 선택 입력/파생 | Executor Streams·Inbox routing |
| EW_EXECUTOR_EVENT_STREAM | 서비스 추가(예제 명시) | Executor Streams·Inbox routing |
| EW_HEALTH_PORT | 서비스 추가(예제 명시) | Executor Streams·Inbox routing |
| EW_IDLE_POLL_SECONDS | 서비스 추가(예제 명시) | Executor Streams·Inbox routing |
| EW_INGRESS_CONCURRENCY | 서비스 추가(예제 명시) | Executor Streams·Inbox routing |
| EW_INSTANCE_ID | 선택 입력/파생 | Executor Streams·Inbox routing |
| EW_LEASE_RENEW_SECONDS | 서비스 추가(예제 명시) | Executor Streams·Inbox routing |
| EW_LEASE_TTL_SECONDS | 서비스 추가(예제 명시) | Executor Streams·Inbox routing |
| EW_MAX_HANDLER_ATTEMPTS | 서비스 추가(예제 명시) | Executor Streams·Inbox routing |
| EW_NAMESPACE | 서비스 추가(예제 명시) | Executor Streams·Inbox routing |
| EW_POLL_SECONDS | 서비스 추가(예제 명시) | Executor Streams·Inbox routing |
| EW_POOL_SIZE | 서비스 추가(예제 명시) | Executor Streams·Inbox routing |
| EW_REQUEST_TIMEOUT_SECONDS | 서비스 추가(예제 명시) | Executor Streams·Inbox routing |
| EW_SHUTDOWN_SECONDS | 서비스 추가(예제 명시) | Executor Streams·Inbox routing |
| EXECUTOR_BASE_URL | 서비스 추가(예제 명시) | Executor HTTP·제출·결과/보고서 |
| EXECUTOR_HTTP_CONNECT_TIMEOUT_SECONDS | 서비스 추가(예제 명시) | Executor HTTP·제출·결과/보고서 |
| EXECUTOR_HTTP_MAX_CONNECTIONS | 서비스 추가(예제 명시) | Executor HTTP·제출·결과/보고서 |
| EXECUTOR_HTTP_MAX_RESPONSE_BYTES | 서비스 추가(예제 명시) | Executor HTTP·제출·결과/보고서 |
| EXECUTOR_HTTP_POOL_TIMEOUT_SECONDS | 서비스 추가(예제 명시) | Executor HTTP·제출·결과/보고서 |
| EXECUTOR_OPERATION_TIMEOUT_SECONDS | 서비스 추가(예제 명시) | Executor HTTP·제출·결과/보고서 |
| EXECUTOR_OPERATION_WAIT_TIMEOUT_SECONDS | 서비스 추가(예제 명시) | Executor HTTP·제출·결과/보고서 |
| EXECUTOR_REPORT_APPEND_TO_NOTEBOOK | 서비스 추가(예제 명시) | Executor HTTP·제출·결과/보고서 |
| EXECUTOR_REPORT_SOURCE_TYPE | 서비스 추가(예제 명시) | Executor HTTP·제출·결과/보고서 |
| EXECUTOR_RESULT_READ_MODE | 서비스 추가(예제 명시) | Executor HTTP·제출·결과/보고서 |
| EXECUTOR_RUNTIME_PROFILE | 서비스 추가(예제 명시) | Executor HTTP·제출·결과/보고서 |
| EXECUTOR_RUNTIME_PROFILES | 서비스 추가(예제 명시) | Executor HTTP·제출·결과/보고서 |
| EXECUTOR_SHARED_INPUT_ROOT | 서비스 추가(예제 명시) | Executor HTTP·제출·결과/보고서 |
| EXECUTOR_SHARED_RESULT_ROOT | 서비스 추가(예제 명시) | Executor HTTP·제출·결과/보고서 |
| EXECUTOR_SOURCE_TYPE | 서비스 추가(예제 명시) | Executor HTTP·제출·결과/보고서 |
| EXECUTOR_SUBMIT_ENABLED | 서비스 추가(예제 명시) | Executor HTTP·제출·결과/보고서 |
| EXECUTOR_TIMEOUT_SECONDS | 서비스 추가(예제 명시) | Executor HTTP·제출·결과/보고서 |
| EXECUTOR_TLS_VERIFY | 서비스 추가(예제 명시) | Executor HTTP·제출·결과/보고서 |
| GRAPH_CHECKPOINTER | 서비스 추가(예제 명시) | LangGraph checkpoint runtime |
| LANGGRAPH_STRICT_MSGPACK | 서비스 추가(예제 명시) | LangGraph checkpoint runtime |
| LLM_RETRY_BACKOFF_SECONDS | 선택 입력/파생 | 기본/선택 모델·Agent builder |
| LLM_TOKEN_BUFFER_MAX_BYTES | 서비스 추가(예제 명시) | 기본/선택 모델·Agent builder |
| LLM_TOKEN_BUFFER_MAX_ITEMS | 서비스 추가(예제 명시) | 기본/선택 모델·Agent builder |
| LLM_TOKEN_ENQUEUE_TIMEOUT_SECONDS | 서비스 추가(예제 명시) | 기본/선택 모델·Agent builder |
| LLM_TOKEN_FLUSH_CHARACTERS | 서비스 추가(예제 명시) | 기본/선택 모델·Agent builder |
| LLM_TOKEN_FLUSH_INTERVAL_SECONDS | 서비스 추가(예제 명시) | 기본/선택 모델·Agent builder |
| LLM_TOKEN_WRITE_TIMEOUT_SECONDS | 서비스 추가(예제 명시) | 기본/선택 모델·Agent builder |
| MAX_PLAN_CANDIDATES | 서비스 추가(예제 명시) | 계획·HITL·분석 맥락·프로젝트 메모리 |
| MODEL_CATALOG | 선택 입력/파생 | 기본/선택 모델·Agent builder |
| MODEL_ENABLE_THINKING | 선택 입력/파생 | 기본/선택 모델·Agent builder |
| MODEL_MAX_OUTPUT_TOKENS | 선택 입력/파생 | 기본/선택 모델·Agent builder |
| MODEL_MAX_RETRIES | 서비스 추가(예제 명시) | 기본/선택 모델·Agent builder |
| MODEL_MOCK_DELAY_MS | 선택 입력/파생 | 기본/선택 모델·Agent builder |
| MODEL_PROVIDER | 서비스 추가(예제 명시) | 기본/선택 모델·Agent builder |
| MODEL_STRUCTURED_OUTPUT_MODE | 서비스 추가(예제 명시) | 기본/선택 모델·Agent builder |
| MODEL_TEMPERATURE | 서비스 추가(예제 명시) | 기본/선택 모델·Agent builder |
| MODEL_TIMEOUT_SECONDS | 서비스 추가(예제 명시) | 기본/선택 모델·Agent builder |
| PHOENIX_PROJECT_NAME | 서비스 추가(예제 명시) | Phoenix/실행 진단 |
| REDIS_URL | 서비스 추가(예제 명시) | CRUD·명령·Store DB/공용 Redis 접속 |
| RUN_CLEANUP_TIMEOUT_SECONDS | 서비스 추가(예제 명시) | app.py·Worker·SSE·종료 |
| RUN_DIAGNOSTICS_DIR | 선택 입력/파생 | Phoenix/실행 진단 |
| RUN_DIAGNOSTICS_STALL_SECONDS | 서비스 추가(예제 명시) | Phoenix/실행 진단 |
| RUN_MONITOR_TIMEOUT_SECONDS | 서비스 추가(예제 명시) | app.py·Worker·SSE·종료 |
| SERVER_HOST | 서비스 추가(예제 명시) | app.py·Worker·SSE·종료 |
| SERVER_RELOAD | 서비스 추가(예제 명시) | app.py·Worker·SSE·종료 |
| SHUTDOWN_DRAIN_SECONDS | 서비스 추가(예제 명시) | app.py·Worker·SSE·종료 |
| SHUTDOWN_TIMEOUT_SECONDS | 서비스 추가(예제 명시) | app.py·Worker·SSE·종료 |
| SQL_ECHO | 서비스 추가(예제 명시) | CRUD·명령·Store DB/공용 Redis 접속 |
| SSE_EVENT_BATCH_SIZE | 서비스 추가(예제 명시) | app.py·Worker·SSE·종료 |
| SSE_HEARTBEAT_SECONDS | 서비스 추가(예제 명시) | app.py·Worker·SSE·종료 |
| SSE_MAX_CONNECTIONS | 서비스 추가(예제 명시) | app.py·Worker·SSE·종료 |
| SSE_POLL_INTERVAL_SECONDS | 서비스 추가(예제 명시) | app.py·Worker·SSE·종료 |
| SSE_RECONCILE_INTERVAL_SECONDS | 서비스 추가(예제 명시) | app.py·Worker·SSE·종료 |
| SSO_ADAPTER_FACTORY | 서비스 추가(예제 명시) | SSO 쿠키·Redis 로그인 세션·사내 SDK adapter |
| SSO_ALLOWED_ORIGINS | 서비스 추가(예제 명시) | SSO 쿠키·Redis 로그인 세션·사내 SDK adapter |
| SSO_ALLOWED_RETURN_ROOTS | 서비스 추가(예제 명시) | SSO 쿠키·Redis 로그인 세션·사내 SDK adapter |
| SSO_AUTO_REGISTER | 서비스 추가(예제 명시) | SSO 쿠키·Redis 로그인 세션·사내 SDK adapter |
| SSO_CALL_TIMEOUT_SECONDS | 서비스 추가(예제 명시) | SSO 쿠키·Redis 로그인 세션·사내 SDK adapter |
| SSO_COOKIE_NAME | 서비스 추가(예제 명시) | SSO 쿠키·Redis 로그인 세션·사내 SDK adapter |
| SSO_COOKIE_SAMESITE | 서비스 추가(예제 명시) | SSO 쿠키·Redis 로그인 세션·사내 SDK adapter |
| SSO_COOKIE_SECURE | 서비스 추가(예제 명시) | SSO 쿠키·Redis 로그인 세션·사내 SDK adapter |
| SSO_FRONTEND_ORIGIN | 서비스 추가(예제 명시) | SSO 쿠키·Redis 로그인 세션·사내 SDK adapter |
| SSO_NAMESPACE | 서비스 추가(예제 명시) | SSO 쿠키·Redis 로그인 세션·사내 SDK adapter |
| SSO_PUBLIC_API_ORIGIN | 서비스 추가(예제 명시) | SSO 쿠키·Redis 로그인 세션·사내 SDK adapter |
| SSO_REDIS_MAX_CONNECTIONS | 서비스 추가(예제 명시) | SSO 쿠키·Redis 로그인 세션·사내 SDK adapter |
| SSO_REDIS_TIMEOUT_SECONDS | 서비스 추가(예제 명시) | SSO 쿠키·Redis 로그인 세션·사내 SDK adapter |
| SSO_SESSION_TTL_SECONDS | 서비스 추가(예제 명시) | SSO 쿠키·Redis 로그인 세션·사내 SDK adapter |
| TASK_CANCEL_POLL_INTERVAL_SECONDS | 서비스 추가(예제 명시) | app.py·Worker·SSE·종료 |
| TASK_LEASE_SECONDS | 서비스 추가(예제 명시) | app.py·Worker·SSE·종료 |
| TASK_RECONCILER_ENABLED | 서비스 추가(예제 명시) | app.py·Worker·SSE·종료 |
| TASK_RECONCILE_INTERVAL_SECONDS | 서비스 추가(예제 명시) | app.py·Worker·SSE·종료 |
| WORKFLOW_DATABASE_URL | 선택 입력/파생 | Workflow 파일·테스트 데이터/아티팩트 |
| WORKFLOW_EMBEDDING_API_KEY | 선택 입력/파생 | Workflow embedding·HNSW 검색 |
| WORKFLOW_EMBEDDING_BASE_URL | 선택 입력/파생 | Workflow embedding·HNSW 검색 |
| WORKFLOW_EMBEDDING_BATCH_SIZE | 서비스 추가(예제 명시) | Workflow embedding·HNSW 검색 |
| WORKFLOW_EMBEDDING_CONCURRENCY | 서비스 추가(예제 명시) | Workflow embedding·HNSW 검색 |
| WORKFLOW_EMBEDDING_DIMENSIONS | 선택 입력/파생 | Workflow embedding·HNSW 검색 |
| WORKFLOW_EMBEDDING_MODEL | 선택 입력/파생 | Workflow embedding·HNSW 검색 |
| WORKFLOW_EMBEDDING_MODEL_REVISION | 서비스 추가(예제 명시) | Workflow embedding·HNSW 검색 |
| WORKFLOW_EMBEDDING_TIMEOUT_SECONDS | 서비스 추가(예제 명시) | Workflow embedding·HNSW 검색 |
| WORKFLOW_PERSISTENCE_ENABLED | 서비스 추가(예제 명시) | Workflow 파일·테스트 데이터/아티팩트 |
| WORKFLOW_RECOMMENDATION_ENABLED | 서비스 추가(예제 명시) | Workflow embedding·HNSW 검색 |
| WORKFLOW_SEARCH_BATCH_SIZE | 서비스 추가(예제 명시) | Workflow embedding·HNSW 검색 |
| WORKFLOW_SEARCH_CANDIDATE_LIMIT | 서비스 추가(예제 명시) | Workflow embedding·HNSW 검색 |
| WORKFLOW_SEARCH_CONTEXT_MAX_CHARS | 서비스 추가(예제 명시) | Workflow embedding·HNSW 검색 |
| WORKFLOW_SEARCH_EF_SEARCH | 서비스 추가(예제 명시) | Workflow embedding·HNSW 검색 |
| WORKFLOW_SEARCH_MAX_ROUNDS | 서비스 추가(예제 명시) | Workflow embedding·HNSW 검색 |
| WORKFLOW_SEARCH_MAX_SCAN_TUPLES | 서비스 추가(예제 명시) | Workflow embedding·HNSW 검색 |
| WORKFLOW_SEARCH_SCAN_MEM_MULTIPLIER | 서비스 추가(예제 명시) | Workflow embedding·HNSW 검색 |
| WORKFLOW_SEARCH_TIMEOUT_MS | 서비스 추가(예제 명시) | Workflow embedding·HNSW 검색 |
| WORKFLOW_SIMILARITY_SCORE | 서비스 추가(예제 명시) | Workflow embedding·HNSW 검색 |
| WORKFLOW_STORAGE_ROOT | 서비스 추가(예제 명시) | Workflow 파일·테스트 데이터/아티팩트 |

선택/파생의 EW_DATABASE_URL, EW_EVENT_GROUP_NAME, EW_INSTANCE_ID는 일반적으로 생략한다. 기본 대상/namespace에서 파생한다. EW_INSTANCE_ID를 지정해도 startup UUID를 붙여 process consumer 충돌을 막는다. DEFAULT_MODEL/MODEL_CATALOG는 다중 모델을 제공할 때 사용하며 PRIVATE_LLM_*는 기본 모델이다. WORKFLOW_PERSISTENCE_ENABLED=false는 legacy Workflow 저장 비활성 명시값이며 추천 embedding 설정과 별개다.

## 플랫폼 소유 유지

다음 키는 삭제하거나 우리 역할/쿠키/SSE로 재해석하지 않는다. loader는 실제 소비 모델에 없는 값을 snapshot에 넣지 않으며 플랫폼 키를 별도 허용 목록으로 관리하지 않는다. 아래 목록은 설명용 예시일 뿐이다. 새 플랫폼 키나 예전 철자도 시작을 막지 않고 `unused_config_keys`에 이름만 표시한다.

`A2A_AGENT_URL`, `A2A_STREAMING_ENABLED`, `API_OUTPUT_MARKDOWN`, `API_OUTPUT_STREAM`, `API_TOKEN`, `CUBE_BOT_EMP_ID`, `CUBE_BOT_TOKEN_ID`, `CUBE_OUTPUT_MARKDOWN`, `CUBE_OUTPUT_STREAM`, `DEFAULT_WORKFLOW`, `GAIA_API_SESSION_NAME`, `GAIA_CUBE_ROUTER_CALL_BACK`, `GAIA_OUTPUT_MARKDOWN`, `GAIA_OUTPUT_STREAM`, `IS_SECURITY_SERVICE`(기존 전달 철자 `IS_SECURITY_SERVCE`도 허용), `RERANKER_API_KEY`, `RERANKER_MODEL`, `S3_AWS_ACCESS`, `S3_AWS_SECRET_ACCESS_KEY`, `S3_BUCKET_NAME`, `S3_ENDPOINT_URL`, `S3_FILE_GATEWAY_URL`, `S3_FILE_ROUTE_AUTH_REQUIRED`, `S3_FILE_ROUTE_PATH`, `S3_FILE_URL_ENABLED`, `S3_FILE_URL_EXPIRES_IN`, `S3_REGION_NAME`, `SECRET_KEY`, `SERVICE_ID`, `SYSTEM_ADMIN`

## 제거·이행

| 항목 | 조치 | 이유 |
|---|---|---|
| AGENT_HISTORY_MESSAGE_LIMIT | 제거; 남아 있으면 시작 오류 | 메시지 수 대신 SET_MAX_HISTORY 턴 수 사용 |
| config.cicd.dev.yml | 제거 | 같은 환경을 별도 이름의 profile로 관리하지 않음; CICD 환경 파일 예제는 cicd/basic/dev/config.dev.example.yml |
| config.yml + 환경별 YAML 병합 | 제거 | config.yml은 로컬 전용, 환경별 파일은 독립 |
| 신규 service/runtime/llm 등 YAML 계층 | 파서·진단 도구에서 제거 | 내부 템플릿과 동일한 최상위 대문자 포맷만 사용 |
| SERVER_PORT/MODEL_NAME/API_BASE_URL/MODEL_API_KEY 추가 블록 | 중복 선언 제거 | 템플릿 PORT/PRIVATE_LLM_* 재사용; 기존 env는 호환 |
| 플랫폼 app의 우리 Phoenix register/shutdown | 제거 | 플랫폼 lifecycle 소유권 보존 |

기존 Redis graph dispatch·Jupyter 직접 접속·topic memory 제거 설정도 계속 오류다. 상세 목록은 src/service_runtime/settings_migrations.py의 폐기 설정 이행 검사를 따른다. DB/Redis/Executor API·메모리 저장 schema·Run/SSE 공개 명세는 이번 설정 변경으로 바뀌지 않는다.

## 100 설정 구조 정리 추적

AGENT_KEYS·EXTRA_KEYS·GROUPS·PLATFORM_ONLY_KEYS·중앙 ALIASES·별도 검색 SETTING_FIELDS를 제거했다. 실제 모델 필드 선언으로 소비 키와 별칭을 유도한다. Agent의 수동 문자열 파서를 타입·기본값·검증이 있는 dataclass로 교체했으며 기존 dataclasses.replace 기반 모델 선택/테스트도 유지한다. 신규 플랫폼 키와 오타 철자를 위해 코드나 허용 목록을 수정할 필요가 없다. 공개 예제의 IS_SECURITY_SERVICE·S3_FILE_URL_EXPIRES_IN 철자를 정정했다. 폐기한 우리 설정의 이행 안내만 별도 작은 모듈에 유지한다.

## Executor API 경로 설정 제거 (103)

Executor v1 경로는 `src/dtest/infrastructure/executor/routes.py`에서 관리한다. 제출·상세·operation·result·notebook·finalize·cancel·artifact·events 경로를 YAML/env로 주입하지 않는다. `EXECUTOR_BASE_URL`에는 서버 root 또는 프록시 root만 넣고 `/api/v1`을 붙이지 않는다. 기존 `EXECUTOR_*_PATH`, `EXECUTOR_JOBS_PATH`, `EW_EXECUTOR_EVENTS_PATH`를 제거한다. 해당 폐기 키가 남아 있으면 값 노출 없이 삭제 안내 오류를 반환한다. 이 항목은 파일 제출 방식인 `EXECUTOR_SOURCE_TYPE=PATH`나 공유 PV root 설정과는 별개다.
