# 구조 리뷰 (2026-10-03)

- **기준**: `feature/refactor-base` @ `e8d65a1`
- **범위**: 프로젝트 구조, 디렉토리, 소스 설계, 시스템 아키텍처, 배포 구성. 라인 단위 버그 리뷰는 범위 밖이다.
- **실행 확인**: `src/agent_service/agents/analysis/tests`와 `src/api_service/test/test_package_boundaries.py`, 합계 314개 통과. PostgreSQL/Redis가 필요한 통합 테스트는 실행하지 않았다.
- **관련 제안**: [실행 Worker 단일화 제안](2026-10-03-worker-execution-unification.md)

## 검토 응답표

| ID | 우선순위 | 항목 | 상태 | 의견 |
|---|---|---|---|---|
| R-01 | P0 | 배포 토폴로지·실행 경로 불일치 | 수용 | 단일 컨테이너·단일 bootstrap 정본으로 정리. uvicorn 직기동은 조기 SIGTERM drain 훅을 우회하며 lifespan 자체가 없는 것은 아님. [응답](2026-10-03-review-response.md#배포와-설정) |
| R-02 | P0 | 예제 설정이 기동 실패하거나 의도와 다르게 동작 | 수용 | 포트 우선순위·loadtest profile·checkpoint alias 충돌 재현. YAML > env 정책은 유지하고 예제와 빌드 경로 수정. --check-config 외 manifest·이미지 검증 필요. |
| R-03 | P0 | uvicorn worker 수만큼 백그라운드 루프 증식 | 부분 수용 | 프로세스 배수 효과·고정 consumer ID 문제 수용. 멀티프로세스 자체는 버그가 아니며 현 배포 정본은 1프로세스 권장. 별도 Deployment는 현재 제약과 불일치. |
| R-04 | P1 | `RunService.create`의 이중 책임 | 수용 | 접수·실행·취소 책임과 도메인 예외 분리. Worker 통합에 앞서 공개 실행 인터페이스와 공통 결과 반영 경계를 추출. |
| R-05 | P1 | api_service 내부 레이어링·트랜잭션 패턴 | 부분 수용 | 쿼리 소유·commit 경계 통일 수용. UoW는 짧은 DB 작업 단위이며 그래프/LLM/HTTP 전체에 연결·트랜잭션을 유지하지 않음. |
| R-06 | P1 | `PlanningState` 77필드 평면 구조·체크포인트 크기 | 부분 수용 | 77필드·관찰 누적·설정별 topology 확인. nested state 자체는 용량 최적화가 아님. saver는 복합 채널의 변경 버전만 blob 저장하므로 크기 공식 정정 필요. |
| R-07 | P1 | API가 Agent state 키를 문자열로 의존 | 부분 수용 | projection 계약 강화 수용. extract_graph_events는 현재 agentic runtime에서 우회하며 public_events 경로가 이미 존재. 현재/레거시 의존을 나누어 정리. |
| R-08 | P1 | 설정 스키마 4종·가려진 기본값·top-level 모듈 | 부분 수용 | 중복 기본값·전역 설정 접근 정리 수용. 현재 주입 원천은 이미 하나이고 역할별 설정 모델 자체는 허용. 역할별 typed snapshot을 명시 주입. |
| R-09 | P1 | DB 연결·Worker·소유권 메커니즘 다중화 | 부분 수용 | 공통 조립부·실행 경로·연결 예산 통합 수용. 내장 Worker는 이미 graph/pool 공유. lease별 보호 대상이 다르므로 중복 이름만으로 제거하지 않음. |
| R-10 | P1 | Agent Runtime 조립부 반복·패키지 순환 | 부분 수용 | 역할 조립·검증 의존 방향 정리 수용. 역할별 agent.py·독립 prompt 보존. 내부 응답 schema까지 전부 service_contracts로 이동하지 않음. |
| R-11 | P2 | 1.3 레거시 코드가 테스트에서만 사용됨 | 부분 수용 | 실제 미사용 1.3 실행 코드 제거 검토 수용. workflow 자산/작성 경로 보존. Dataset draft는 보류된 기능 계약이며 검증 script에서도 사용. |
| R-12 | P2 | `workflow/` 업무 자산과 코드 혼재 | 부분 수용 | tmp·생성 도구 역할 정리 수용. analysis/workflow 위치 유지. 현재 툴은 AST로 읽고 실행하지 않으며 __init__ 삭제 시 wheel 소스 포함 검증 필요. |
| R-13 | P2 | 죽은 코드·루트 잡동사니·의존성 선언 | 수용 | router 문법 오류 AST 재현·직접 dotenv 의존 누락 확인. 플랫폼 예제 보존 위치와 실제 adapter를 구분하고 불필요 코드/의존성 정리. |
| R-14 | P2 | 벤치마크 원본 데이터로 저장소 비대화 | 부분 수용 | 신규 대용량 원문은 외부 artifact 보관 권장. 기존 검증 근거는 이관·checksum 검증 전 삭제하지 않으며 Git 이력 재작성은 이번 범위 밖. |
| R-15 | P2 | 테스트 구조(conftest 부재 등) | 수용 | 공용 fixture와 명시적 helper·DB/Redis marker 정리. 단위/통합 테스트 경계 보강. 디렉토리 전면 이동·unittest 일괄 전환은 필수 아님. |

## 총평

큰 경계는 잘 잡혀 있다.

- Agent 패키지가 API를 import하지 않는다는 규칙은 AST 검사와 별도 인터프리터 import 차단, 두 가지 테스트로 검증된다.
- 설정 우선순위는 `service_settings.load_settings` 한 곳에만 구현돼 있고, 결과 snapshot은 frozen이다.
- Redis 이벤트 파이프라인(Ingress → inbox → Router → outbox → Dispatcher)은 응집돼 있다.

문제는 각 패키지의 **내부**다.

- 짧은 기간에 기능이 연속으로 추가되면서 api_service 내부 레이어링, Agent 상태 설계, 설정 스키마가 흐트러졌다.
- 이전 1.3 구조의 코드가 대량으로 남아 있다.
- 배포 영역은 문서, 매니페스트, 실제 실행 코드가 서로 다른 구성을 가리킨다.

---

## P0. 운영 위험

### R-01. 배포 토폴로지와 실행 경로 불일치 (확인)

**근거**

- 문서마다 배포 구성이 다르다.
  - [service-layout.md](../architecture/service-layout.md): 단일 Deployment, 단일 컨테이너 Pod를 전제로 한다.
  - [deploy/dtest-agent.yaml](../../deploy/dtest-agent.yaml): Deployment를 2개(api, worker) 둔다.
  - [cicd/basic/dev/deployment.yml](../../cicd/basic/dev/deployment.yml): Pod 하나에 컨테이너 2개(API + worker sidecar)를 둔다.
- 루트 `Dockerfile`의 CMD는 `python app.py`인데, compose와 deploy는 모두 `uvicorn main:app --app-dir /app/src`로 덮어쓴다.
  - uvicorn으로 직접 기동하면 `service_bootstrap.build_server`의 SIGTERM drain 훅을 거치지 않는다.
- deploy 프로브는 `/health`를 본다. `/service/ready`와 `/service/live`는 쓰지 않는다.

**제안**

- 실행 경로를 `service_bootstrap.main` 하나로 통일한다(`app.py` 또는 `dtest-agent-api`).
- 매니페스트는 한 세트만 정본으로 둔다. 나머지는 삭제하거나 "참고용"으로 명시한다.
- 프로브는 readiness와 liveness 전용 엔드포인트로 바꾼다.

### R-02. 예제 설정이 기동에 실패하거나 의도와 다르게 동작 (확인)

**근거**

- [config.yml](../../config.yml)의 `server_port: 8000`이 환경변수보다 우선한다.
  - 그래서 cicd의 `SERVER_PORT=5000`이 무시된다. 반면 containerPort, 프로브, Service는 5000을 기대한다.
- [deploy/secret.example.yaml:8-9](../../deploy/secret.example.yaml)의 `CHECKPOINT_DB_URI`와 `AGENT_CHECKPOINT_DATABASE_URL` 값이 다르다(`?sslmode=disable` 유무).
  - 이 경우 `service_settings.py:121`이 `Conflicting aliases`를 내고 기동이 중단된다.
- [compose.loadtest.yaml:6](../../compose.loadtest.yaml)의 `APP_ENV: loadtest`는 로더가 거부하는 값이다(`service_settings.py:222`, dev/stg/prd만 허용).
- cicd Dockerfile에 문제가 여러 개 있다.
  - `requirements.txt`로 설치하는데, 이 파일에 `asyncpg`, `redis`, `aiohttp`가 없다.
  - `ENV ... RUN_MIGRATIONS_ON_START = true`는 문법 오류다.
  - 주석에 나오는 `runtime_requirements.py`는 존재하지 않는다.
- cicd 매니페스트에 오타가 있다.
  - `redinessProbe`, `volumnMounts`, `successThreshod`, `DB_WAIT_SECODNS`, `lables`
  - 이미지/URL 이름이 `detest-`와 `dtest-`로 섞여 있다.
- `PHOENIX_CONFIG_PATH`는 `.env.example`과 cicd에 남아 있지만 로더가 읽지 않는다.

**제안**

- 모든 예제 env와 매니페스트의 환경값에 대해 `python app.py --check-config`를 실행하는 CI 검사를 추가한다.
- 의존성 설치는 `uv.lock` 하나를 기준으로 하고(`uv sync --frozen`), `requirements*.txt`는 생성물로 두거나 삭제한다.

### R-03. uvicorn worker 수만큼 백그라운드 루프 증식 (확인 / 영향은 추론)

**근거**

- compose는 `--workers ${..:-4}`로 기동한다.
- [service_bootstrap.py:114](../../src/service_bootstrap.py)의 `_background_factories`는 프로세스마다 `agent-run-worker`와 `task-lock-reconciler`를 띄운다.
  - stg/prd 설정에서 둘 다 활성이므로 Pod 하나에 루프가 4개씩 생긴다.
- 그 결과 실행 동시성(`AGENT_WORKER_CONCURRENCY`)이 웹 worker 수와 곱해진다.
- `EW_INSTANCE_ID`가 고정값(`dtest-agent-uid`)이다. replica가 늘어나면 Redis consumer 이름이 충돌할 수 있다(추론).

**제안**

- 백그라운드 루프는 웹 프로세스에서 분리한다(별도 프로세스, 또는 `--workers 1` 고정).
- instance id는 Pod 이름이나 hostname에서 파생한다.

---

## P1. 설계 구조

### R-04. `RunService.create`의 이중 책임 (확인)

**근거**

- [run_service.py](../../src/api_service/services/run_service.py)는 912줄이고, static method 21개를 가진 클래스 하나로 되어 있다. `create()`는 약 400줄이다(L306~).
- 비공개 플래그 `_execute_existing` 하나로 두 경로를 전환한다.
  - API 접수: 세션 잠금, 멱등 재생, admission
  - Worker 실행: lease heartbeat, graph 호출, 취소, 종료 반영
  - 이 플래그를 넘기는 호출자는 `agent_run_worker.py:135` 하나뿐이다.
- 이 파일에 `HTTPException`이 30회 쓰인다.
  - Worker 경로도 `HTTPException`을 던지고, `_is_retryable`이 이를 보고 재시도를 판단한다.
- 라우트가 비공개 메서드를 호출한다(`api/v1/routes/runs.py:93` → `RunService._session`).
- `executor_completion.py`도 `RunService._lock_run_and_task`, `_finish_run`, `_finalize_state`를 외부에서 호출한다.

**제안**

- 다음 세 모듈로 분리한다.
  - `run_admission`: 생성, 재생, 검증
  - `run_execution`: 실행, 종료, 오류 기록. `agent_run_worker` 소유로 둔다.
  - `run_cancellation`
- 도메인 예외를 정의하고, HTTP 변환은 `core/problems.py` 한 곳에서만 한다.

### R-05. api_service 내부 레이어링과 트랜잭션 패턴 (확인)

**근거**

- 레이어 위반
  - route 모듈 8개가 모두 SQLAlchemy를 import하고, 6개는 직접 쿼리를 구성한다.
  - route 2개는 repositories를 직접 import한다.
  - services/ 전체에서 `HTTPException`이 132회 쓰인다.
  - repositories/는 5파일, 346줄이고 서비스 36개 중 8개만 사용한다.
- 역방향 의존
  - `repositories/agent_run_repository.py` → `services.helpers`
  - `core/auth.py` → repository
  - `core/execution_lifecycle.py` → `TaskService`(함수 내 import)
- 트랜잭션 패턴이 4가지 공존한다.
  - 요청 단위 `get_db` + 서비스 내부 commit
  - `short_session()`
  - `get_session_factory()()` 직접 호출
  - `commit: bool = True` 플래그(7개 함수, `commit=False` 호출 21곳)
- `test_package_boundaries.py`는 패키지 간 경계만 검사하고, api_service 내부 레이어는 검사하지 않는다.

**제안**

- Unit of Work 객체 하나를 하위로 전달하고, commit은 route나 worker 경계에서만 한다.
- repository 계층을 유지할지 결정한다.
  - 유지한다면 쿼리 구성을 repository로 모은다.
  - 유지하지 않는다면 계층을 없애고 문서에 명시한다.
- 내부 레이어 규칙을 AST 테스트로 추가한다: routes는 ORM 금지, services는 fastapi 금지.

### R-06. `PlanningState` 77필드 평면 구조와 체크포인트 크기 (확인 / 크기 영향은 추론)

**근거**

- [planning/graph.py:21-98](../../src/agent_service/agents/analysis/planning/graph.py)에 필드가 77개 있다. 하위 상태나 reducer는 없다.
  - 그중 44개가 실행, repair, Executor 대기 관련 필드인데 상태 이름은 "Planning"이다.
- `receive` 노드가 필드 약 50개를 수동으로 초기화한다.
- 체크포인트에 쌓이는 내용
  - 승인 snapshot에 함수 원문과 Skill 문서 전체가 들어간다(`service_contracts/plan_review.py`).
  - repair 후에는 `execution_snapshot`에 사본이 하나 더 생긴다.
  - `observations`는 누적되고, 읽을 때만 잘라낸다.
  - 따라서 체크포인트 크기가 대략 "단계 수 × snapshot 크기"로 늘어난다(추론).
- 실행 노드는 `runtime.execution_enabled`일 때만 연결된다(graph.py:296).
  - 설정에 따라 그래프 토폴로지가 바뀌므로, 플래그를 변경하면 기존 thread가 다른 토폴로지에서 resume될 수 있다(추론).

**제안**

- 상태를 중첩 TypedDict로 나눈다: request, review, execution, repair, executor_wait.
- 원문은 hash 참조로 저장한다.
- 그래프 조립과 상태 정의를 `analysis/graph.py`, `analysis/state.py`로 분리한다.
- 체크포인트 호환성을 검증하는 단계를 포함한다.

### R-07. API가 Agent state 키를 문자열로 의존 (확인)

**근거**

- [graph_event_persistence.py](../../src/api_service/services/graph_event_persistence.py)의 `extract_graph_events`(217줄)는 agent state 키 24개를 문자열로 직접 읽는다.
  - import가 없어서 경계 테스트는 통과하지만, "API는 Agent state에 의존하지 않는다"는 문서 규칙을 실질적으로 위반한다.
- 이미 제거된 노드 이름(`workflow_recommender`, `workflow_generator`)이 분기문에 남아 있다(`graph_event_persistence.py:127`, `chat_crud_message_sink.py:139`).

**제안**

- 상태에서 공개 이벤트로 바꾸는 projection 계약을 `service_contracts`에 정의한다.
- Agent가 그 계약 형태로 이벤트를 내보내도록 한다.

### R-08. 설정 스키마 4종, 가려진 기본값, top-level 모듈 (확인)

**근거**

- 설정 값 모델이 4개다.
  - `config.Settings`: pydantic, 57필드
  - `agent_config.AgentSettings`: dataclass, 72필드, 수동 파싱
  - `event_worker_settings.Settings`: 24필드
  - `SsoSettings`: 14필드
- API 기본값이 먼저 주입되기 때문에 agent 쪽 기본값은 적용되지 않는다. 예:
  - `MODEL_MAX_RETRIES`: agent 2 → 실효 0
  - `EXECUTOR_SUBMIT_ENABLED`: agent True → 실효 False
  - `EXECUTOR_SHARED_INPUT_ROOT`: `/workspace/shared` → `/workspace/pv`
- `config`, `main`, `agent_config` 등 7개 모듈이 top-level `py-modules`로 설치된다(pyproject.toml).
  - `config`와 `main`은 충돌 위험이 높은 이름이다.
- 공용 계층이 이 top-level 설정 모듈을 직접 import한다. 경계 테스트는 이 경로를 검사하지 않는다.
  - `integrations/executor/{client,manifest}.py`
  - `service_runtime/{diagnostics,model_selection}.py`
  - `agent_service/runtime/{model_factory,langgraph/checkpointer}.py`
- 설정 접근 방식이 service locator 형태다(`from config import settings`).
  - `src/main.py`는 import 시점에 `create_app()`을 호출한다.

**제안**

- 중첩 그룹을 가진 pydantic 모델 하나로 통합하고 YAML 그룹과 1:1로 맞춘다.
- 가려진 기본값은 삭제한다.
- 7개 모듈을 하나의 패키지(예: `dtest_service/`)로 이동한다.
- 공용 계층에는 snapshot을 명시적으로 주입한다.

### R-09. DB 연결, Worker, 소유권 메커니즘 다중화 (확인)

**근거**

- 한 프로세스에 Postgres 연결 보유자가 최대 6개다.
  - SQLAlchemy+asyncpg 엔진(`core/database.py`)
  - asyncpg LISTEN 연결(`run_stream_service.py`)
  - psycopg pool 4개: ExecutorWorker, ApiWorkerBridge, LangGraph checkpointer, memory Store
- "worker"라는 이름의 개념이 3개다.
  - `agent_run_worker.py`: Postgres Run 큐
  - `worker/`: Redis inbox/outbox
  - `agent_worker/`: graph 재개 어댑터와 독립 진입점
- 소유권과 lease 메커니즘이 4개다.
  - Redis `SessionGuard`
  - DB `SessionExecution`
  - Task lease
  - consumer lock lease
- graph 조립 코드가 3곳에 중복된다.
  - `services/agent_graph_service.py:77`
  - `agent_worker/graph_provider.py:24`
  - `worker_main.graph_context`의 비공유 분기

**제안**

- graph 팩토리를 하나로 통합한다.
- 이름을 역할 기준으로 바꾼다(예: `run_queue/`, `event_bus/`, `graph_resume/`).
- 어떤 lease가 무엇을 보호하는지 표로 문서화한다.
- 구조적 해법은 [실행 Worker 단일화 제안](2026-10-03-worker-execution-unification.md)을 참고한다.

### R-10. Agent Runtime 조립부 반복과 패키지 순환 (확인)

**근거**

- [planning/runtime.py](../../src/agent_service/agents/analysis/planning/runtime.py)에 캐시 3개(`agents`, `revision_agents`, `execution_agents`)와 역할별 if/elif 분기, inline validator가 있다.
- 응답 스키마가 4곳에 분산돼 있다: conversation agent 내부 동적 생성, `planning/proposals.py`, 각 role의 `agent.py`, `service_contracts`.
- 패키지 순환이 3개 있다.
  - planning ↔ agent_builders
  - execution ↔ agent_builders
  - runtime ↔ middleware ↔ factory
  - 이 순환들은 함수 내 import로 가려져 있다.
- 함수 내 import는 api_service에만 74개(20파일) 있다.
- mock provider 분기가 운영 코드 안에 있다(`runtime.py`, `graph.py`).

**제안**

- 역할 레지스트리를 둔다: role → (builder, schema, validator).
- 캐시는 하나로 합치고 키를 (role, model, revision, variant)로 한다.
- 응답 스키마는 한 모듈에 모은다.

---

## P2. 정리·위생

### R-11. 1.3 레거시 코드가 테스트에서만 사용됨 (확인)

**근거**

- agent_service의 비테스트 코드 약 7.8k줄 중 런타임 그래프에서 import로 도달 가능한 코드는 약 40%다.
- 다음 모듈(합계 약 2.7k줄)은 **테스트에서만** import된다. api_service와 devtools에서는 사용하지 않는다.
  - `workflow/workflow_compiler.py`
  - `rule_based_notebook_generator.py`
  - `data_load_steps.py`
  - `execution_notebook_reader.py`
  - `adaptive_workflow.py`
  - `tools/catalog.py`
  - `schemas/workflows/workflow_plan_format.py`
- 다음 모듈은 테스트를 포함해 어디서도 import되지 않는다.
  - `workflow_recommender.py`(None을 반환하는 stub)
  - `schemas/skills/skill_format.py`
- 같은 registry를 읽는 catalog가 2개다: `tools/catalog.py`, `planning/catalog.py`의 AssetCatalog.
- step 출력을 바인딩하는 compiler도 2개다: `workflow/workflow_compiler.py`, `execution/compiler.py`.
- `service_contracts/dataset_registry_draft.py`(16KB)는 운영 패키지에 있지만 사용처가 테스트 하나뿐이다.

**제안**

- `analysis/legacy_v13/`로 격리하거나, 대응 테스트와 함께 삭제한다.
- draft 계약은 `docs/design` 또는 별도 브랜치로 옮긴다.

### R-12. `workflow/`에 업무 자산과 코드가 혼재 (확인)

**근거**

- skills/tools 자산 디렉토리에 `__init__.py`가 13개 있다. 그래서 분석 함수가 import 가능한 모듈로 배포된다(pandas, matplotlib 의존).
- `src` 아래에 `tmp/` 파일 11개가 커밋돼 있다.
- 자산 생성기(약 500줄)가 런타임 패키지 안에 있다.
- 잘된 점: 생성된 `skill_index.yaml`, `tool_registry.yaml`의 drift 테스트가 있다.

**제안**

- 자산은 `__init__.py` 없는 데이터 디렉토리로 옮긴다.
- 생성기는 `devtools`로 옮긴다.
- `tmp/`는 `src` 밖으로 옮긴다.

### R-13. 죽은 코드, 루트 잡동사니, 의존성 선언 (확인)

**근거**

- 죽은 코드
  - [src/routers/chat/router.py:3](../../src/routers/chat/router.py)에 문법 오류(`import typing import List`)가 있다. 존재하지 않는 모듈을 import하며, 아무 곳에서도 사용하지 않는다.
- 루트 파일
  - `run.py`는 `app.py`와 중복이다.
  - `hitl_sample.py`는 Langflow 샘플로, 이 프로젝트와 무관하다.
  - `개선사항.txt`, `데이터추출tool예제.ipynb`, `wf_packages.txt`(참조 없음), `requirements.crud.txt`(참조 없음)가 있다.
- 의존성 선언
  - `pydantic-settings`는 선언돼 있지만 import가 0건이다.
  - `python-dotenv`는 사용 중인데 미선언이다(전이 의존으로만 설치된다).

**제안**

- 죽은 코드와 무관 파일은 삭제한다.
- 개인 메모와 노트북은 `docs/` 하위나 저장소 밖으로 옮긴다.
- 의존성 선언을 실제 사용과 맞춘다.

### R-14. 벤치마크 원본 데이터로 저장소 비대화 (확인)

**근거**

- `.git`이 약 110MB, `docs/`가 약 115MB다.
- 대부분 `docs/reports/**/raw*.json.gz` 벤치마크 원본이다. 단일 파일이 최대 20MB이고, `docs/reports` 아래에 파일이 373개 있다.

**제안**

- 원본은 git 외부(artifact 저장소, 릴리스 첨부 등)로 옮기고 요약과 재현 방법만 남긴다.
- 이미 커밋된 이력을 정리할지는 별도로 판단한다(force push가 필요하므로 기본 작업 원칙과 충돌한다).

### R-15. 테스트 구조 (확인)

**근거**

- `conftest.py`와 pytest 설정이 없다.
  - 대신 테스트 파일 29개가 다른 테스트 모듈에서 fixture를 import한다(`test_user_identity_postgres` 26회, `test_run_cleanup_postgres` 20회).
- unittest와 pytest가 혼용된다.
- DB가 필요한 테스트를 구분하는 marker가 없다.
- `agent_service/.../tests`는 `api_service.services.graph_crud_persistence`를 import한다. 경계 테스트가 `tests` 경로를 건너뛰기 때문에 허용되는 상태다.

**제안**

- 공용 harness를 `conftest.py`로 옮긴다.
- `[tool.pytest.ini_options]`에 postgres와 redis marker를 추가한다.
- 최상위 `tests/` 디렉토리로 옮기는 것을 검토한다.

---

## 잘 되어 있는 점

- 패키지 경계 테스트: AST 검사와 서브프로세스 import 차단으로 이중 검증한다.
- 설정 우선순위가 로더 한 곳에만 구현돼 있고, frozen snapshot을 쓰며, 재초기화를 거부한다.
- Redis 이벤트 파이프라인이 응집돼 있다(inbox 중복 제거, outbox, DLQ).
- 생성된 registry/index 파일에 drift 테스트가 있다.
- 역할별 `agent.py`가 얇게 유지돼 있다.

## 제안 진행 순서

1. **R-01~R-03**: 배포와 설정 정합성. 작업량은 작고 위험도는 가장 높다.
2. **R-11, R-13, R-14**: 레거시 격리와 저장소 정리. 이후 리팩토링 범위가 줄어든다.
3. **R-04, R-05**: `RunService` 분리와 Unit of Work 통일.
4. **R-09**와 [Worker 단일화 제안](2026-10-03-worker-execution-unification.md)의 1단계.
5. **R-06, R-07**: 상태 구조화와 projection 계약. 체크포인트 호환성 검증이 필요하다.
6. **R-08, R-10**: 설정 통합, 패키지화, 역할 레지스트리.

## 정정란

(작성 이후 사실관계가 바뀌거나 틀린 내용이 확인되면 여기에 기록한다.)

## 개발 검토 응답 (2026-10-03)

원문은 보존하고 위 응답표에 판단을 기록했다. 재현 결과, 사실관계 보완, 합의된 제약과의 차이는 [상세 응답](2026-10-03-review-response.md)에 정리했다. 수용은 설계 방향에 대한 판단이며 구현 완료를 뜻하지 않는다.
