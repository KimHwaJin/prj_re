# 기동과 설정

현재 YAML 초기화·이전·실행은 [097 앱 설정 안내](application-configuration.md), 배포 구조·포트·전환 절차는 [통합 배포 안내](deployment-configuration.md)를 따른다. 아래는 bootstrap의 설계 설명이며 갱신된 구현이 우선한다.

루트 `app.py`가 `src/service_bootstrap.py`를 호출한다. 설정은 `src/service_settings.py`에서 프로세스당 한 번 확정한다. 기존 `run.py`, `uvicorn main:app --app-dir src`도 같은 bootstrap을 사용한다. 025에서 API 패키지는 `src/api_service`로 이동했다. 루트 `app.py`는 그대로 진입점이며 기존 패키지 이름 충돌용 우회는 제거했다.

서비스와 같은 설정으로 새 HTML을 `/demo`에서 제공한다. [클론 후 로컬 설정·schema·화면 실행](service-demo-console.md)을 참고한다.

## 실행

```sh
python scripts/configure.py init --env dev
# config.dev.yml의 실제 연결값을 수정한 뒤 검증
python app.py --env dev --check-config
python app.py --env dev
# 이전 dev dotenv 보조 입력은 필요할 때만 --local-env-file .env
python app.py --config /mounted/config.yml
```

`APP_ENV=dev|stg|prd` 또는 `--env`로 환경을 선택한다. `development/staging/production` 환경변수 값도 허용한다. 기본 포트는 8000이다. `--check-config`는 설정 출처와 Worker 활성 여부만 출력하고 서버나 외부 연결을 시작하지 않는다.

공통 config.yml은 앱 정책을 명시하며 실제 profile은 example에서 초기화한다. 선택 profile이 없으면 시작 오류다. API와 Worker 활성값은 공통 YAML에 있고 dev 예제의 실제 Executor 제출은 꺼져 있다. DB schema는 사전 준비한다. API만 실행하려면 세 Worker flag를 false로 지정한다. Executor 이벤트 수신은 `event_worker_enabled`, 실제 제출은 `executor_submit_enabled`로 각각 지정한다. API 쓰기 요청까지 차단하는 읽기 전용 모드는 아니다.

Docker 기본 CMD도 `python app.py`로 변경했다. 058에서 Compose·Kubernetes·CICD의 명시적 Uvicorn 명령과 별도 Worker 배포를 제거하고 app.py 한 프로세스로 통일했다. 이번 단계에서 기존 컨테이너를 재기동하거나 새 이미지를 배포하지 않았다. 013에서 프로세스별 제한된 Run 동시 실행을 구현했다. 최종 Pod의 프로세스 수와 배포 명령은 별도 적용·검증이 필요하다.

## 우선순위

항목마다 `config.{환경}.yml > config.yml > 환경변수 > 기본값`이다. 로컬에서 명시적으로 `--local-env-file`을 제공하면 해당 파일은 환경변수보다 낮은 순위로 들어간다. `.env` 자동 탐색이나 전역 `os.environ` 변경은 하지 않는다. 로컬 dotenv는 dev에서만 허용하고 `${...}` 변수 확장을 하지 않는다.

`--config` 또는 `SERVICE_CONFIG_FILE`을 지정하면 그 파일 하나를 사용하며 공통/환경별 YAML 자동 병합은 하지 않는다. 테스트에서 `load_settings(config={}, environ={})`는 파일과 프로세스 환경에서 완전히 격리된다.

명시된 `false`, `0`은 유지한다. **YAML에 false가 있으면 환경변수 true로 바뀌지 않는다.** 환경변수를 통한 변경이 필요한 항목은 YAML에서 생략한다. 잘못된 명시값은 시작 오류이며 기본값으로 복구하지 않는다. 동일 소스의 서로 다른 별칭 값도 오류다. 설정을 바꾸려면 프로세스를 재시작한다.

```yaml
service:
  runtime:
    agent_worker_enabled: true
    task_reconciler_enabled: true
    event_worker_enabled: false
  llm:
    model_provider: mock
    model_mock_delay_ms: 200
  checkpoint:
    checkpoint_setup_on_start: false
  executor:
    executor_submit_enabled: false
```

`service` 아래 그룹은 runtime/database/checkpoint/llm/agent/executor/events/storage/diagnostics/auth/workflow_search이고, leaf key는 환경변수 이름과 같다(대소문자 무관). 플랫폼 YAML의 다른 최상위 설정은 그대로 둘 수 있다. `service` 안의 알 수 없는 key는 오류다. `service`가 없으면 문서 전체를 서비스 설정으로 해석한다. 상대 파일 경로는 기존 소비 코드의 해석을 유지하므로 배포 PV 경로에는 절대 경로를 사용한다.

## Run 동시 실행 수

`AGENT_WORKER_CONCURRENCY`는 **API Run 실행기 프로세스 하나가 동시에 처리할 그래프 호출 수**다. 기본값은 1이며 1 이상의 정수만 허용한다. 한 세션의 전체 대화나 장기 Executor 작업 수가 아니다. 다른 세션의 Run을 동시에 진행하며, HITL/Executor 대기로 그래프 호출이 반환되면 자리를 돌려준다. 같은 세션은 대기 중에도 새 입력이 제한되고, 사용자 HITL 응답만 resume할 수 있다. Executor 대기는 이벤트 Worker가 재개한다.

```yaml
service:
  runtime:
    agent_worker_enabled: true
    agent_worker_concurrency: 4
```

4는 설정 예시이며 운영 권장값을 확정한 것이 아니다. 환경변수로 조정하려면 공통/환경 YAML 양쪽의 해당 키를 생략한 뒤 `AGENT_WORKER_CONCURRENCY=4`를 주입하고 재시작한다. YAML에 1이 명시되어 있으면 환경변수 4보다 우선한다. `--check-config` 출력에서 해석된 값을 확인할 수 있다.

Run 실행기가 프로세스마다 활성화된 경우 최대 동시 호출 수는 대략 `프로세스별 설정 × API 프로세스 수 × Pod 수`다. Executor 이벤트 Worker의 실행량은 이 값에 포함되지 않는다. 따라서 이 설정은 전역 LLM/DB 사용량 제한이 아니다. DB 풀·LLM 한도와 실제 대기 시간/처리량을 함께 측정해 조절해야 한다. 기존 Docker 컨테이너 설정은 이번 작업에서 바꾸지 않았다.

한 실행기의 queue 점유 조회는 한 번에 하나만 진행하며 자리가 없으면 새 Run을 미리 점유하지 않는다. 종료 시 점유 중인 요청과 실행 중인 자식 작업을 정리한다. 종료 확인/소유권이 불확실해지면 새 점유를 중단하고 기존 복구 필요 정책을 적용한다. 즉시 안전한 자동 재실행이나 강제 coroutine 종료를 제공하는 설정은 아니다. [013 검증 기록](improvements/013-run-concurrency.md)을 참고한다.

## LLM 설정

`MODEL_PROVIDER`는 `openai_compatible`(기본값) 또는 부하테스트용 `mock`만 지원한다. 실제 모델은 `MODEL_NAME`, `API_BASE_URL`, `MODEL_API_KEY`로 선택한다. YAML에서는 `service.llm` 아래 같은 이름을 소문자로 사용할 수 있다. 모델별 timeout/retry/structured output 설정과 기존 설정 우선순위는 유지한다.

007에서 Azure 지원을 제거했다. `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT`, `AZURE_OPENAI_API_VERSION`, `LLM_API_VERSION`은 더 이상 설정으로 등록되지 않는다. YAML에 남아 있으면 unknown setting 오류로 시작을 거부하므로 삭제한다. 환경변수/명시적 로컬 dotenv에 남아 있는 미등록 키는 기존 로더 정책대로 무시하며 모델을 자동 선택하지 않는다. `MODEL_PROVIDER=azure_openai` 또는 동일한 `LLM_PROVIDER` 별칭 값은 지원하지 않는 provider 오류다.

## DB별 책임과 별칭

| 설정 | 소비자 | 생략 시 |
|---|---|---|
| DATABASE_URL | API SQLAlchemy / CRUD Alembic | dev의 기존 로컬 기본값; stg/prd는 명시 필수 |
| CHECKPOINT_DB_URI | Agent 실행 / API 상태 읽기 / Executor 이벤트 resume의 checkpointer | dev의 공통 로컬 기본값; stg/prd는 명시 필수 |
| EW_DATABASE_URL | 공통 DB의 이벤트 Inbox·binding / 제출 bridge / Worker Alembic | DATABASE_URL에서 드라이버를 psycopg용으로 정규화 |
| WORKFLOW_DATABASE_URL | Workflow catalog/history | EW_DATABASE_URL |

`AGENT_CHECKPOINT_DATABASE_URL`은 `CHECKPOINT_DB_URI`의 별칭이다. 두 이름을 서로 다른 값으로 주면 시작을 거부한다. 역할별 DB를 하나로 강제하지 않으며, 같은 checkpoint를 읽는 소비자들의 설정을 하나로 맞춘다. URL은 실제 자격증명 없이 위 표의 이름으로만 진단한다.

`EW_REDIS_URL`은 생략 시 `REDIS_URL`, `EW_EXECUTOR_BASE_URL`은 `EXECUTOR_BASE_URL`을 따른다. 명시된 잘못된 EW 설정을 다른 DB로 바꾸는 fallback은 제거했다. Workflow 저장을 끄려면 `WORKFLOW_PERSISTENCE_ENABLED=false`를 명시한다. 단순 URL 누락으로 조용히 Null 저장소가 선택되지 않는다.

`LLM_*`의 기존 모델 관련 이름은 `MODEL_*`/`API_BASE_URL`과 공통 별칭으로 처리한다. `EXECUTOR_JOBS_PATH`는 `EXECUTOR_EXECUTIONS_PATH` 별칭이다. `PHOENIX_CONFIG_PATH`로 별도 파일을 재탐색하지 않으므로 `PHOENIX_ENDPOINT/PHOENIX_API_KEY`를 중앙 YAML 또는 환경변수로 옮긴다. 아직 Agent 모델 팩터리가 사용하지 않는 API 전용 필드(예: 최대 출력 토큰)를 이번 단계에서 새 모델 기능으로 연결하지 않았다.

## 수명과 플랫폼 경계

`create_app()`은 로컬 앱을 만들고 `create_app(platform_app=..., settings=...)`은 이미 조립된 FastAPI에 붙인다. `attach_service()`는 기존 앱/라우터 lifespan을 보존하면서 Worker 시작·종료와 공유 자원 정리를 합성한다. 두 번 붙이면 오류다. startup 실패 시 시작된 형제 작업을 정리한다. 015부터 정상 종료는 새 점유를 중단하고 현재 호출에 유예 시간을 준 뒤, 남은 작업만 취소한다. Run 내부 종료 경로는 [001 개선](improvements/001-run-cleanup-stall.md)에서 stop 신호와 종료 기한 관찰로 변경했다. 종료가 확인되지 않은 background 작업이 있으면 서비스 공용 자원을 먼저 닫지 않는다. 종료 기한 초과는 오류로 드러내며, 취소를 무시하는 Python 코루틴을 강제 종료하는 기능은 아니다.

`/health`는 기존 생존 확인이다. `/service/ready`는 소유한 background loop가 끝났거나 Run 종료/소유권 불확실성을 감지하면 503으로 바뀐다. 새 `/service/live`도 Run 건전성 실패를 503으로 노출한다. Kubernetes probe 연결은 배포 측에서 별도 검증해야 한다. 058에서 내장 이벤트 consumer·이벤트 DB/Redis와 활성 API 실행기의 tasks 테이블 확인을 추가했다. 모델·Executor와 모든 테이블의 종합 검증은 아니다.

`run_cleanup_timeout_seconds`(기본 5초)는 정상 stop/취소 후 종료 관찰, `run_monitor_timeout_seconds`(기본 3초)는 취소 감시·heartbeat DB 작업에 적용한다. `service.runtime` YAML 또는 동일한 대문자 환경변수 이름으로 설정한다. LLM·Executor 작업 제한 시간이 아니다. 종료가 확인되지 않는 작업은 복구 필요 상태를 유지하며 자동 재실행되지 않는다. 자세한 운영 제한은 001 기록을 따른다.

[004 개선](improvements/004-graph-resource-lifecycle.md)부터 API/Run 그래프의 checkpoint·binding 풀은 첫 사용에 한 번 열고 서비스 lifespan 동안 재사용한다. Executor 이벤트 그래프·checkpoint 풀도 이벤트마다 생성하지 않고 Worker lifespan 동안 유지한다. 내장 모드는 056부터 API와 이벤트가 같은 graph/checkpointer를 사용한다. API SQLAlchemy·Store·binding/event pool까지 하나로 합친 것은 아니다. 풀 크기의 합과 Pod 수를 고려해 DB 연결 예산을 검증해야 한다.

그래프를 사용하는 호출이 남아 있으면 `SHUTDOWN_TIMEOUT_SECONDS`까지 반환을 기다리고 새 사용은 거절한다. 반환이 확인되지 않으면 다른 서비스 풀도 먼저 닫지 않는다. HITL/Executor 대기로 그래프 호출이 반환된 상태는 자원 차용 중으로 세지 않으며, 대기 세션마다 checkpoint 연결을 하나씩 보유하지 않는다. 초기 그래프에 고정된 설정·의존성·catalog prompt 변경은 재시작으로 반영한다.

플랫폼의 앱·라우터·미들웨어·OpenAPI·계측 및 lifespan 설정이 끝난 **후**에 결합해야 한다. 이후 `GaiaService.main()`이 lifespan을 다시 덮어쓰면 안 된다. 실제 Gaia 원본이 없으므로 템플릿의 main 초기화 분리/계측 보존까지 검증한 상태는 아니다. 플랫폼 core는 수정하지 않았다.

## 정상 종료 유예

`SHUTDOWN_DRAIN_SECONDS`(기본 20초)는 현재 호출의 정상 완료 유예, `SHUTDOWN_TIMEOUT_SECONDS`(기본 25초)는 취소 후 정리 관찰 시간이다. `service.runtime` YAML 또는 같은 대문자 환경변수로 설정한다. drain은 0도 허용하며 YAML의 명시값이 환경변수보다 우선한다. 두 값은 `--check-config`에 표시된다.

루트 `python app.py`는 SIGTERM을 받는 즉시 readiness를 내리고 API Run/이벤트 Worker의 새 점유를 중단한다. 이미 시작된 호출의 heartbeat/소유권은 유지하다 정상 반환 후 해제한다. HITL·Executor 대기로 반환하면 더 기다리지 않는다. 유예 초과 시 취소·복구 필요 정책을 적용하며, 정리 기한도 넘기면 실행 중인 작업 아래에서 풀을 먼저 닫지 않는다.

직접 `uvicorn main:app`을 쓰면 lifespan에서 drain은 적용되지만 SIGTERM 즉시 Worker에 알리는 루트 launcher hook은 적용되지 않는다. 플랫폼의 별도 launcher도 같은 hook 연결이 필요하다. 기존 Compose·실행 중인 컨테이너는 변경하지 않았다. 플랫폼의 종료 예산과 자원 정리 여유를 함께 확인해야 하며, 기본값을 운영 확정값으로 보지 않는다. [015의 검증·진입점 제한](improvements/015-graceful-shutdown.md)을 참고한다.

## 공통 세션 실행 소유권

014부터 API Run과 Executor 이벤트가 API `DATABASE_URL`의 `session_executions`를 공통으로 사용한다. 모든 실행자의 이 설정이 같은 DB를 가리켜야 한다. `EW_DATABASE_URL`의 이벤트 저장소, `CHECKPOINT_DB_URI`의 checkpoint 저장소는 기존 역할을 유지한다. 이벤트 Redis를 제거한 변경이 아니다.

CRUD migration `20260929_0020`이 필요하다. 첫 적용에서는 이전 Worker를 배수·중지하고 새 코드로 전환해야 한다. 구 버전과 신 버전을 섞어 실행하는 rollout은 안전성을 보장하지 않는다. 실행 소유권은 heartbeat 만료만으로 자동 탈취하지 않으며, 강제 종료 후에는 기록이 남아 운영 복구가 필요할 수 있다. [014의 배포·복구 제한](improvements/014-session-execution-ownership.md)을 확인한다.

## 마이그레이션

두 Alembic env와 로컬 bootstrap이 동일한 중앙 설정을 사용한다. 로컬 bootstrap은 YAML 우선순위까지 적용한 실제 대상이 Compose의 postgres/chat_app·agent인지 확인하고 나서만 초기화한다. 일반 서버 시작은 checkpoint DDL을 실행하지 않도록 예제에서 꺼 두었다. 기존 로컬 bootstrap은 checkpoint setup을 명시적으로 수행한다. 새 DB의 테이블 생성과 실제 업그레이드는 배포 준비 단계에서 별도 수행한다.

외부 연결 없이 SQL만 확인하려면:

```sh
PYTHONPATH=src python -m alembic -c alembic.crud.ini upgrade head --sql
PYTHONPATH=src python -m alembic -c alembic.ini upgrade head --sql
```

`worker_past`, `workflow/tools/data_io/tmp/extract_data2.py`, 독립 부하테스트 도구는 이번 서비스 설정 통합 범위에 포함하지 않는다. source/Docker 실행을 검증 대상으로 삼았으며 기존 wheel packaging 구조는 후속 패키지 분리 때 정비한다.

## 그래프 실행과 서비스 DB 연결

Run 실행 준비·결과 저장은 짧은 서비스 DB 트랜잭션으로 처리한다. 실행 준비의 시작 이벤트 commit 후에는 refresh로 새 트랜잭션을 열지 않는다. `ainvoke_user_turn`·`ainvoke_resume`·`astream_user_turn`은 열린 `db`를 받지 않고 일반 실행 값만 받는다. 테스트/별도 조립에서 필요한 경우 `session_factory=`를 주입할 수 있으며, 기본값은 기존 프로세스 공용 SQLAlchemy 풀을 사용한다.

프로젝트 설정 조회와 각 그래프 상태 투영은 `short_session`으로 별도 세션을 열고 닫는다. 이는 새 풀/연결을 매번 생성하는 방식이 아니다. 세션이 필요한 순간 공용 풀에서 연결을 빌리고, 그래프 실행·checkpoint 접근·스트림 소비자 대기 전에는 반납한다. 체크포인터와 Executor bridge의 기존 풀은 별개로 유지한다. heartbeat/취소 감시도 필요한 순간에는 짧게 서비스 DB를 사용한다.

이 변경은 동일 세션의 업무 잠금을 해제하지 않는다. 모델 대기 중 연결 반환과 세션 입력 허용은 서로 다른 문제다. 새 설정이나 DB migration은 필요하지 않다. 연결 1개 테스트는 회귀 검증 조건이며 운영 풀 크기 권장값은 아니다.

### 여러 LLM 모델 등록

`MODEL_CATALOG`와 `DEFAULT_MODEL`도 이 중앙 로더에서만 해석한다.
[Run 모델 선택 설정](run-model-selection.md)의 예시와 우선순위·장기 Run 배포 정책을 따른다.

### Executor HTTP 연결 예산

Executor HTTP는 런타임에서 생성·재사용한다. 연결 수·연결/풀 대기 기한·응답 크기 설정과
복구 필요 상태의 의미는 [Executor HTTP 런타임](executor-http-runtime.md)을 따른다.

## 038 계획 Agent 설정

`service.agent` 그룹에 `max_plan_candidates`, `agent_discovery_max_rounds`, `agent_history_message_limit`, `analysis_datasets`를 둘 수 있다. 대응 env는 MAX_PLAN_CANDIDATES, AGENT_DISCOVERY_MAX_ROUNDS, AGENT_HISTORY_MESSAGE_LIMIT, ANALYSIS_DATASETS다. YAML mapping은 중앙 설정에서 JSON으로 변환되므로 env에서는 JSON 문자열을 사용한다. 상세 기본값·제한·데이터 scope는 [계획 Runtime 안내](agentic-planning-runtime.md)를 참고한다. Phoenix도 중앙 PHOENIX_ENDPOINT/PROJECT_NAME/API_KEY를 사용하고 API lifespan에서 시작·종료한다.


040의 AGENT_REPAIR_LEVEL(기본 0), AGENT_REPAIR_LEVEL_LIMIT(기본 4), AGENT_MAX_REPAIR_ATTEMPTS(기본 3)도 `service.agent`에서 중앙 주입한다. Workflow 명시 정책을 먼저 유지하고 없는 항목만 중앙 기본값으로 채운다. 의미·범위·승인 예시는 [오류 수정 설정](agentic-execution-repair.md#중앙-설정)을 따른다. 개별 Agent가 별도로 os.environ을 읽지 않는다.


041의 `AGENT_FREE_PLAN_ENABLED`(기본 true), `AGENT_FREE_PLAN_REQUIRE_APPROVAL`(기본 true), `AGENT_MAX_PLAN_REVISIONS`(기본 5, 1~20)도 `service.agent`에서 중앙 주입한다. 실행 전 사용자 재작성 이후의 자유 코드 허용/확인과 Run 공통 재작성·질문 답변 횟수이며 실행 실패 수정 설정과 독립이다. 승인 생략은 완전한 자유 후보 하나에만 적용한다. [재작성 설정과 예외](agentic-plan-revision.md#중앙-설정)를 따른다.

## 045 SSO 설정

`service.auth`의 `SSO_*` 설정도 같은 중앙 loader에서 YAML > env > 기본값으로 주입한다.
SDK factory·API/프론트 origin·SSO 허용 주소는 실제 환경에서 제공해야 한다. 미설정 로그인은
503이며 X-User-Id 우회는 없다. 자동 일반 사용자 등록, 고정 로그인 TTL, 쿠키 정책, 로그인
Redis 연결풀의 설정·주석과 Swagger 테스트는 [SSO 가이드](sso-authentication.md)를 따른다.

## 프로젝트 메모리 한도와 입력 예산

`service.agent`의 `AGENT_PROJECT_MEMORY_*`는 중앙 설정에서 API 저장 정책·Agent 응답 schema·미들웨어에 동일하게 주입한다. 기본값과 문자/추정 토큰 단위, 0의 의미, 역할별 범위, 한도 변경 시 기존 문서 처리 정책은 [프로젝트 메모리](project-memory.md)를 따른다. config.yml에 각 설정의 주석 예시가 있다. 설정 변경은 프로세스 재시작 후 적용된다.

060 현재 실행은 API·Inbox·명령 원장에 같은 DB 정본을 사용한다. 별도 Event DB override는 실행 활성 상태에서 거절하며 자료 이행은 [공통 Worker 안내](agent-command-worker.md)를 따른다. 내부 Redis command/group·dispatch/publish-lease 설정은 삭제되었고 남은 YAML/env 입력은 오류다.


## Workflow 임베딩·HNSW 설정

API와 Agent는 한 ServiceSettings.workflow_search snapshot을 공유한다. 채팅 모델 API와 임베딩 모델 API는 별도다. 모델 설정 세 항목(BASE_URL/MODEL/DIMENSIONS)을 함께 제공하고 secret·revision·검색 예산은 [config.yml](../config.yml)의 주석, 색인 이행은 [확정 계약](workflow-registration-and-search.md)을 따른다. 별도 .env 로더·별도 Workflow 검색 DB 풀을 만들지 않았다. 검색 DB는 DATABASE_URL의 CRUD DB이며 과거 WORKFLOW_DATABASE_URL의 독립 legacy catalog를 검색하지 않는다.
