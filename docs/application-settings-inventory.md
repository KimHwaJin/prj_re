# 앱 설정 식별과 이전 목록

2026-10-06 / 097. 중앙 loader가 허용하는 정식 이름 전체를 소스와 대조했다. 별칭을 제외한 171개 중 공통 정책 132개, dev profile에 명시한 연결·환경값 20개, 선택 입력/파생/호환 19개다. stg/prd도 같은 환경별 key 집합을 사용한다. 실제 개인 credential을 이 목록에 기록하지 않는다.

leaf는 소문자로 YAML에 쓰고 그룹은 설명용이다. 그룹 변경이 별도 설정 공간을 만들지 않는다. API·Agent·Worker가 src/service_settings.py에서 해석한 snapshot을 공유한다. [사용법·이전 명령](application-configuration.md)을 따른다.

## 이전한 정식 항목

| 정식 설정 | YAML 그룹 | 위치 | 소비 영역 |
|---|---|---|---|
| AGENT_DISCOVERY_MAX_ROUNDS | agent | config.yml | PlanningRuntime·HITL·메모리 middleware |
| AGENT_FREE_PLAN_ENABLED | agent | config.yml | PlanningRuntime·HITL·메모리 middleware |
| AGENT_FREE_PLAN_REQUIRE_APPROVAL | agent | config.yml | PlanningRuntime·HITL·메모리 middleware |
| AGENT_HISTORY_MESSAGE_LIMIT | agent | config.yml | PlanningRuntime·HITL·메모리 middleware |
| AGENT_MAX_OPERATIONS | agent | config.yml | PlanningRuntime·HITL·메모리 middleware |
| AGENT_MAX_PLAN_REVISIONS | agent | config.yml | PlanningRuntime·HITL·메모리 middleware |
| AGENT_MAX_REPAIR_ATTEMPTS | agent | config.yml | PlanningRuntime·HITL·메모리 middleware |
| AGENT_OBSERVATION_MAX_CHARS | agent | config.yml | PlanningRuntime·HITL·메모리 middleware |
| AGENT_PROJECT_MEMORY_MAX_CHARS | agent | config.yml | PlanningRuntime·HITL·메모리 middleware |
| AGENT_PROJECT_MEMORY_MAX_UPDATES | agent | config.yml | PlanningRuntime·HITL·메모리 middleware |
| AGENT_PROJECT_MEMORY_MODE | agent | config.yml | PlanningRuntime·HITL·메모리 middleware |
| AGENT_PROJECT_MEMORY_PATCH_MAX_CHARS | agent | config.yml | PlanningRuntime·HITL·메모리 middleware |
| AGENT_PROJECT_MEMORY_PROMPT_MAX_CHARS | agent | config.yml | PlanningRuntime·HITL·메모리 middleware |
| AGENT_PROJECT_MEMORY_PROMPT_MAX_TOKENS | agent | config.yml | PlanningRuntime·HITL·메모리 middleware |
| AGENT_REPAIR_LEVEL | agent | config.yml | PlanningRuntime·HITL·메모리 middleware |
| AGENT_REPAIR_LEVEL_LIMIT | agent | config.yml | PlanningRuntime·HITL·메모리 middleware |
| AGENT_SESSION_ANALYSIS_MAX_CHARS | agent | config.yml | PlanningRuntime·HITL·메모리 middleware |
| AGENT_WORKER_CONCURRENCY | runtime | config.yml | app.py / API Run·command Worker / SSE |
| AGENT_WORKER_ENABLED | runtime | config.yml | app.py / API Run·command Worker / SSE |
| AGENT_WORKER_MAX_RETRIES | runtime | config.yml | app.py / API Run·command Worker / SSE |
| AGENT_WORKER_NOTIFY_ENABLED | runtime | config.yml | app.py / API Run·command Worker / SSE |
| AGENT_WORKER_POLL_INTERVAL_SECONDS | runtime | config.yml | app.py / API Run·command Worker / SSE |
| AGENT_WORKER_RECONCILE_INTERVAL_SECONDS | runtime | config.yml | app.py / API Run·command Worker / SSE |
| AGENT_WORKER_RETRY_BACKOFF_SECONDS | runtime | config.yml | app.py / API Run·command Worker / SSE |
| AGENT_WORKER_RETRY_MAX_BACKOFF_SECONDS | runtime | config.yml | app.py / API Run·command Worker / SSE |
| ANALYSIS_DATASETS | agent | config.yml | PlanningRuntime·HITL·메모리 middleware |
| API_BASE_URL | llm | config.<환경>.yml | 모델 선택·role agent builder |
| API_V1_PREFIX | runtime | config.yml | app.py / API Run·command Worker / SSE |
| APP_NAME | runtime | config.yml | app.py / API Run·command Worker / SSE |
| CHECKPOINT_DB_URI | checkpoint | config.<환경>.yml | LangGraph saver와 graph runtime |
| CHECKPOINT_POOL_MAX_SIZE | checkpoint | config.yml | LangGraph saver와 graph runtime |
| CHECKPOINT_POOL_MIN_SIZE | checkpoint | config.yml | LangGraph saver와 graph runtime |
| CHECKPOINT_POOL_TIMEOUT_SECONDS | checkpoint | config.yml | LangGraph saver와 graph runtime |
| CHECKPOINT_SETUP_ON_START | checkpoint | config.yml | LangGraph saver와 graph runtime |
| DATABASE_MAX_OVERFLOW | database | config.yml | CRUD·Store / Redis 접속 |
| DATABASE_POOL_RECYCLE_SECONDS | database | config.yml | CRUD·Store / Redis 접속 |
| DATABASE_POOL_SIZE | database | config.yml | CRUD·Store / Redis 접속 |
| DATABASE_POOL_TIMEOUT_SECONDS | database | config.yml | CRUD·Store / Redis 접속 |
| DATABASE_PREPARED_STATEMENT_CACHE_SIZE | database | config.yml | CRUD·Store / Redis 접속 |
| DATABASE_URL | database | config.<환경>.yml | CRUD·Store / Redis 접속 |
| DATA_MOCK | executor | config.yml | Executor client·코드 제출·결과 읽기 |
| DEMO_ARTIFACTS_ENABLED | storage | config.yml | Workflow catalog/파일·trusted mock 데이터 |
| DEMO_ARTIFACTS_ROOT | storage | config.yml | Workflow catalog/파일·trusted mock 데이터 |
| EVENT_WORKER_ENABLED | runtime | config.yml | app.py / API Run·command Worker / SSE |
| EW_BATCH_SIZE | events | config.yml | Executor Streams 수신·Inbox routing |
| EW_CLAIM_IDLE_MILLISECONDS | events | config.yml | Executor Streams 수신·Inbox routing |
| EW_EXECUTOR_EVENT_STREAM | events | config.yml | Executor Streams 수신·Inbox routing |
| EW_HEALTH_PORT | events | config.yml | Executor Streams 수신·Inbox routing |
| EW_IDLE_POLL_SECONDS | events | config.yml | Executor Streams 수신·Inbox routing |
| EW_INGRESS_CONCURRENCY | events | config.yml | Executor Streams 수신·Inbox routing |
| EW_LEASE_RENEW_SECONDS | events | config.yml | Executor Streams 수신·Inbox routing |
| EW_LEASE_TTL_SECONDS | events | config.yml | Executor Streams 수신·Inbox routing |
| EW_MAX_HANDLER_ATTEMPTS | events | config.yml | Executor Streams 수신·Inbox routing |
| EW_NAMESPACE | events | config.yml | Executor Streams 수신·Inbox routing |
| EW_POLL_SECONDS | events | config.yml | Executor Streams 수신·Inbox routing |
| EW_POOL_SIZE | events | config.yml | Executor Streams 수신·Inbox routing |
| EW_REQUEST_TIMEOUT_SECONDS | events | config.yml | Executor Streams 수신·Inbox routing |
| EW_SHUTDOWN_SECONDS | events | config.yml | Executor Streams 수신·Inbox routing |
| EXECUTOR_ARTIFACTS_PATH | executor | config.yml | Executor client·코드 제출·결과 읽기 |
| EXECUTOR_BASE_URL | executor | config.<환경>.yml | Executor client·코드 제출·결과 읽기 |
| EXECUTOR_CANCEL_PATH | executor | config.yml | Executor client·코드 제출·결과 읽기 |
| EXECUTOR_EXECUTIONS_PATH | executor | config.yml | Executor client·코드 제출·결과 읽기 |
| EXECUTOR_EXECUTION_PATH | executor | config.yml | Executor client·코드 제출·결과 읽기 |
| EXECUTOR_FINALIZE_PATH | executor | config.yml | Executor client·코드 제출·결과 읽기 |
| EXECUTOR_HTTP_CONNECT_TIMEOUT_SECONDS | executor | config.yml | Executor client·코드 제출·결과 읽기 |
| EXECUTOR_HTTP_MAX_CONNECTIONS | executor | config.yml | Executor client·코드 제출·결과 읽기 |
| EXECUTOR_HTTP_MAX_RESPONSE_BYTES | executor | config.yml | Executor client·코드 제출·결과 읽기 |
| EXECUTOR_HTTP_POOL_TIMEOUT_SECONDS | executor | config.yml | Executor client·코드 제출·결과 읽기 |
| EXECUTOR_NOTEBOOK_PATH | executor | config.yml | Executor client·코드 제출·결과 읽기 |
| EXECUTOR_OPERATIONS_PATH | executor | config.yml | Executor client·코드 제출·결과 읽기 |
| EXECUTOR_OPERATION_TIMEOUT_SECONDS | executor | config.yml | Executor client·코드 제출·결과 읽기 |
| EXECUTOR_OPERATION_WAIT_TIMEOUT_SECONDS | executor | config.yml | Executor client·코드 제출·결과 읽기 |
| EXECUTOR_REPORT_APPEND_TO_NOTEBOOK | executor | config.yml | Executor client·코드 제출·결과 읽기 |
| EXECUTOR_REPORT_SOURCE_TYPE | executor | config.yml | Executor client·코드 제출·결과 읽기 |
| EXECUTOR_RESULT_PATH | executor | config.yml | Executor client·코드 제출·결과 읽기 |
| EXECUTOR_RESULT_READ_MODE | executor | config.yml | Executor client·코드 제출·결과 읽기 |
| EXECUTOR_RUNTIME_PROFILE | executor | config.<환경>.yml | Executor client·코드 제출·결과 읽기 |
| EXECUTOR_RUNTIME_PROFILES | executor | config.<환경>.yml | Executor client·코드 제출·결과 읽기 |
| EXECUTOR_SHARED_INPUT_ROOT | executor | config.<환경>.yml | Executor client·코드 제출·결과 읽기 |
| EXECUTOR_SHARED_RESULT_ROOT | executor | config.<환경>.yml | Executor client·코드 제출·결과 읽기 |
| EXECUTOR_SOURCE_TYPE | executor | config.yml | Executor client·코드 제출·결과 읽기 |
| EXECUTOR_SUBMIT_ENABLED | executor | config.<환경>.yml | Executor client·코드 제출·결과 읽기 |
| EXECUTOR_TIMEOUT_SECONDS | executor | config.yml | Executor client·코드 제출·결과 읽기 |
| EXECUTOR_TLS_VERIFY | executor | config.yml | Executor client·코드 제출·결과 읽기 |
| GRAPH_CHECKPOINTER | checkpoint | config.yml | LangGraph saver와 graph runtime |
| LANGGRAPH_STRICT_MSGPACK | checkpoint | config.yml | LangGraph saver와 graph runtime |
| LLM_TOKEN_BUFFER_MAX_BYTES | llm | config.yml | 모델 선택·role agent builder |
| LLM_TOKEN_BUFFER_MAX_ITEMS | llm | config.yml | 모델 선택·role agent builder |
| LLM_TOKEN_ENQUEUE_TIMEOUT_SECONDS | llm | config.yml | 모델 선택·role agent builder |
| LLM_TOKEN_FLUSH_CHARACTERS | llm | config.yml | 모델 선택·role agent builder |
| LLM_TOKEN_FLUSH_INTERVAL_SECONDS | llm | config.yml | 모델 선택·role agent builder |
| LLM_TOKEN_WRITE_TIMEOUT_SECONDS | llm | config.yml | 모델 선택·role agent builder |
| MAX_PLAN_CANDIDATES | agent | config.yml | PlanningRuntime·HITL·메모리 middleware |
| MAX_WORKFLOW_REVISIONS | workflow_search | config.yml | 등록 embedding·HNSW 검색·Agent 추천 |
| MOCK_DATA_ROOT | storage | config.yml | Workflow catalog/파일·trusted mock 데이터 |
| MODEL_API_KEY | llm | config.<환경>.yml | 모델 선택·role agent builder |
| MODEL_MAX_RETRIES | llm | config.yml | 모델 선택·role agent builder |
| MODEL_NAME | llm | config.<환경>.yml | 모델 선택·role agent builder |
| MODEL_PROVIDER | llm | config.yml | 모델 선택·role agent builder |
| MODEL_STRUCTURED_OUTPUT_MODE | llm | config.yml | 모델 선택·role agent builder |
| MODEL_TEMPERATURE | llm | config.yml | 모델 선택·role agent builder |
| MODEL_TIMEOUT_SECONDS | llm | config.yml | 모델 선택·role agent builder |
| PHOENIX_PROJECT_NAME | diagnostics | config.yml | Phoenix/Run 계측 |
| REDIS_URL | database | config.<환경>.yml | CRUD·Store / Redis 접속 |
| RUN_CLEANUP_TIMEOUT_SECONDS | runtime | config.yml | app.py / API Run·command Worker / SSE |
| RUN_DIAGNOSTICS_STALL_SECONDS | diagnostics | config.yml | Phoenix/Run 계측 |
| RUN_MONITOR_TIMEOUT_SECONDS | runtime | config.yml | app.py / API Run·command Worker / SSE |
| SERVER_HOST | runtime | config.<환경>.yml | app.py / API Run·command Worker / SSE |
| SERVER_PORT | runtime | config.<환경>.yml | app.py / API Run·command Worker / SSE |
| SERVER_RELOAD | runtime | config.yml | app.py / API Run·command Worker / SSE |
| SHUTDOWN_DRAIN_SECONDS | runtime | config.yml | app.py / API Run·command Worker / SSE |
| SHUTDOWN_TIMEOUT_SECONDS | runtime | config.yml | app.py / API Run·command Worker / SSE |
| SQL_ECHO | database | config.yml | CRUD·Store / Redis 접속 |
| SSE_EVENT_BATCH_SIZE | runtime | config.yml | app.py / API Run·command Worker / SSE |
| SSE_HEARTBEAT_SECONDS | runtime | config.yml | app.py / API Run·command Worker / SSE |
| SSE_MAX_CONNECTIONS | runtime | config.yml | app.py / API Run·command Worker / SSE |
| SSE_POLL_INTERVAL_SECONDS | runtime | config.yml | app.py / API Run·command Worker / SSE |
| SSE_RECONCILE_INTERVAL_SECONDS | runtime | config.yml | app.py / API Run·command Worker / SSE |
| SSO_ADAPTER_FACTORY | auth | config.<환경>.yml | SSO adapter·Redis 로그인 세션 |
| SSO_ALLOWED_ORIGINS | auth | config.<환경>.yml | SSO adapter·Redis 로그인 세션 |
| SSO_ALLOWED_RETURN_ROOTS | auth | config.yml | SSO adapter·Redis 로그인 세션 |
| SSO_AUTO_REGISTER | auth | config.yml | SSO adapter·Redis 로그인 세션 |
| SSO_CALL_TIMEOUT_SECONDS | auth | config.yml | SSO adapter·Redis 로그인 세션 |
| SSO_COOKIE_NAME | auth | config.yml | SSO adapter·Redis 로그인 세션 |
| SSO_COOKIE_SAMESITE | auth | config.yml | SSO adapter·Redis 로그인 세션 |
| SSO_COOKIE_SECURE | auth | config.<환경>.yml | SSO adapter·Redis 로그인 세션 |
| SSO_FRONTEND_ORIGIN | auth | config.<환경>.yml | SSO adapter·Redis 로그인 세션 |
| SSO_NAMESPACE | auth | config.<환경>.yml | SSO adapter·Redis 로그인 세션 |
| SSO_PUBLIC_API_ORIGIN | auth | config.<환경>.yml | SSO adapter·Redis 로그인 세션 |
| SSO_REDIS_MAX_CONNECTIONS | auth | config.yml | SSO adapter·Redis 로그인 세션 |
| SSO_REDIS_TIMEOUT_SECONDS | auth | config.yml | SSO adapter·Redis 로그인 세션 |
| SSO_SESSION_TTL_SECONDS | auth | config.yml | SSO adapter·Redis 로그인 세션 |
| TASK_CANCEL_POLL_INTERVAL_SECONDS | runtime | config.yml | app.py / API Run·command Worker / SSE |
| TASK_LEASE_SECONDS | runtime | config.yml | app.py / API Run·command Worker / SSE |
| TASK_RECONCILER_ENABLED | runtime | config.yml | app.py / API Run·command Worker / SSE |
| TASK_RECONCILE_INTERVAL_SECONDS | runtime | config.yml | app.py / API Run·command Worker / SSE |
| WORKFLOW_EMBEDDING_BATCH_SIZE | workflow_search | config.yml | 등록 embedding·HNSW 검색·Agent 추천 |
| WORKFLOW_EMBEDDING_CONCURRENCY | workflow_search | config.yml | 등록 embedding·HNSW 검색·Agent 추천 |
| WORKFLOW_EMBEDDING_MODEL_REVISION | workflow_search | config.yml | 등록 embedding·HNSW 검색·Agent 추천 |
| WORKFLOW_EMBEDDING_TIMEOUT_SECONDS | workflow_search | config.yml | 등록 embedding·HNSW 검색·Agent 추천 |
| WORKFLOW_PERSISTENCE_ENABLED | storage | config.yml | Workflow catalog/파일·trusted mock 데이터 |
| WORKFLOW_RECOMMENDATION_ENABLED | workflow_search | config.yml | 등록 embedding·HNSW 검색·Agent 추천 |
| WORKFLOW_SEARCH_BATCH_SIZE | workflow_search | config.yml | 등록 embedding·HNSW 검색·Agent 추천 |
| WORKFLOW_SEARCH_CANDIDATE_LIMIT | workflow_search | config.yml | 등록 embedding·HNSW 검색·Agent 추천 |
| WORKFLOW_SEARCH_CONTEXT_MAX_CHARS | workflow_search | config.yml | 등록 embedding·HNSW 검색·Agent 추천 |
| WORKFLOW_SEARCH_EF_SEARCH | workflow_search | config.yml | 등록 embedding·HNSW 검색·Agent 추천 |
| WORKFLOW_SEARCH_MAX_ROUNDS | workflow_search | config.yml | 등록 embedding·HNSW 검색·Agent 추천 |
| WORKFLOW_SEARCH_MAX_SCAN_TUPLES | workflow_search | config.yml | 등록 embedding·HNSW 검색·Agent 추천 |
| WORKFLOW_SEARCH_SCAN_MEM_MULTIPLIER | workflow_search | config.yml | 등록 embedding·HNSW 검색·Agent 추천 |
| WORKFLOW_SEARCH_TIMEOUT_MS | workflow_search | config.yml | 등록 embedding·HNSW 검색·Agent 추천 |
| WORKFLOW_SIMILARITY_SCORE | workflow_search | config.yml | 등록 embedding·HNSW 검색·Agent 추천 |
| WORKFLOW_STORAGE_ROOT | storage | config.yml | Workflow catalog/파일·trusted mock 데이터 |

## 선택 입력·파생·기본값으로 유지한 항목

별도 DB/중복 endpoint·자동 UUID처럼 무조건 YAML에 복사하면 오히려 갈리는 설정은 생략한다. 실제 embedding/Phoenix를 사용할 때는 환경 profile의 주석 block을 해제하고 값을 넣는다.

| 정식 설정 | 선택 위치 / 의미 |
|---|---|
| APP_ENV | 파일 선택용 --env 또는 env; YAML에 넣지 않음 |
| DEFAULT_MODEL | 환경별 llm; MODEL_CATALOG의 기본 alias. 단일 모델이면 생략 |
| EW_DATABASE_URL | DATABASE_URL에서 psycopg DSN 파생. 활성 command Worker는 같은 DB만 허용 |
| EW_EVENT_GROUP_NAME | events 선택 override; 생략하면 namespace:ingress |
| EW_INSTANCE_ID | events 선택 label prefix; 생략하면 startup UUID. 지정해도 새 UUID를 붙임 |
| EXECUTOR_EVENTS_PATH | executor 선택 override; EXECUTOR_EXECUTION_PATH+/events 파생 |
| LLM_RETRY_BACKOFF_SECONDS | 등록된 API typed 호환 필드; 현재 Agent 모델 재시도 간격에 미연결 |
| MODEL_CATALOG | 환경별 llm; 여러 모델의 endpoint/key/동작별 목록. 단일 모델이면 생략 |
| MODEL_ENABLE_THINKING | 환경별 llm; 지원하는 모델에서만 명시. 미지정은 모델 정책 유지 |
| MODEL_MAX_OUTPUT_TOKENS | 등록된 API typed 호환 필드; 현재 Agent model factory에 미연결. 효력이 있다고 안내하지 않음 |
| MODEL_MOCK_DELAY_MS | 테스트 환경의 llm; mock 응답 지연 ms. 일반 연결에 불필요 |
| PHOENIX_API_KEY | 환경별 diagnostics; collector 인증이 필요한 경우만 지정 |
| PHOENIX_ENDPOINT | 환경별 diagnostics; 실제 collector endpoint. 미지정이면 tracing exporter 생략 |
| RUN_DIAGNOSTICS_DIR | 환경별 diagnostics; 계측 파일 출력 위치. 기본 출력 비활성 |
| WORKFLOW_DATABASE_URL | EW_DATABASE_URL에서 legacy catalog DSN 파생; 현재 추천 검색은 CRUD DATABASE_URL 사용 |
| WORKFLOW_EMBEDDING_API_KEY | 환경별 workflow_search; 임베딩 API 인증이 필요한 경우만 지정 |
| WORKFLOW_EMBEDDING_BASE_URL | 환경별 workflow_search; 모델·차원과 세 항목을 함께 지정 |
| WORKFLOW_EMBEDDING_DIMENSIONS | 환경별 workflow_search; 실제 모델 차원. HNSW 모델 공간과 일치 |
| WORKFLOW_EMBEDDING_MODEL | 환경별 workflow_search; 채팅 MODEL_NAME과 독립 |

## 구 이름과 제외 영역

같은 의미의 구 이름은 정식 이름으로 정규화한다. 한 소스에서 서로 다른 값이면 오류다. 새 YAML은 정식 이름을 사용한다.

| 정식 이름 | 허용하는 기존 별칭 |
|---|---|
| API_BASE_URL | LLM_API_BASE_URL |
| CHECKPOINT_DB_URI | AGENT_CHECKPOINT_DATABASE_URL |
| EW_INGRESS_CONCURRENCY | EW_CONCURRENCY |
| EXECUTOR_BASE_URL | EW_EXECUTOR_BASE_URL |
| EXECUTOR_EVENTS_PATH | EW_EXECUTOR_EVENTS_PATH |
| EXECUTOR_EXECUTIONS_PATH | EXECUTOR_JOBS_PATH |
| MODEL_API_KEY | LLM_API_KEY |
| MODEL_ENABLE_THINKING | LLM_ENABLE_THINKING |
| MODEL_MAX_OUTPUT_TOKENS | LLM_MAX_OUTPUT_TOKENS |
| MODEL_MAX_RETRIES | LLM_MAX_RETRIES |
| MODEL_NAME | LLM_MODEL_NAME |
| MODEL_PROVIDER | LLM_PROVIDER |
| MODEL_STRUCTURED_OUTPUT_MODE | LLM_STRUCTURED_OUTPUT_MODE |
| MODEL_TEMPERATURE | LLM_TEMPERATURE |
| MODEL_TIMEOUT_SECONDS | LLM_TIMEOUT_SECONDS |
| REDIS_URL | EW_REDIS_URL |

삭제된 내부 Redis 명령/dispatch 설정 EW_COMMAND_STREAM_NAME, EW_COMMAND_GROUP_NAME, EW_DISPATCH_CONCURRENCY, EW_PUBLISH_LEASE_SECONDS는 이전 대상이 아니다. 폐기된 Jupyter/Redis 관리 API 설정과 topic별 메모리 한도도 남아 있으면 오류다. Azure 설정을 재도입하지 않는다.

PYTHONUTF8/PYTHONIOENCODING/PYTHONPATH, 이미지·자원·probe·APP_ENV selector는 실행 플랫폼 영역이다. LOCAL_*와 Compose bind mount 치환 값은 인프라 영역이다. 이 값들을 앱 YAML leaf로 넣지 않는다. logger/Gaia의 기존 최상위 YAML은 service와 별개로 보존한다.

기존 독립 benchmark·temporary fixture·모델 전용 진단 dotenv는 명시적 격리 입력으로 유지한다. 실제 앱 설정을 다시 읽는 대체 경로가 아니다. legacy Tool 함수 내부의 파일/환경 접근은 이 중앙 배포 설정 이전과 별도이며 앞으로 등록되는 Tool의 범용 실행 계약을 바꾸지 않는다.

## 적용 범위와 값 변화

공통 Worker concurrency1·CRUD 최대20·checkpoint 최대4 등 기존 기본 예산을 유지한다. 새 일반 profile은 기존 .env.example의 INLINE 제출·operation timeout300초·default kernel을 채택했다. dev 실제 제출 기본은 false이고 stg/prd는 true다. 이전 도구는 기존 .env의 명시값을 그대로 우선 이전한다. 사내 CICD PATH/MANIFEST·HTTP TLS 기존 값과 port5000은 별도 public profile에 보존했다.

이전 작업을 처리량 개선으로 표현하지 않는다. 실제 env 값 없이도 모든 환경 profile을 초기화·검증할 수 있도록 만든 것이며 실제 접속·schema 준비·배포는 설정 확인 후 수행한다.
