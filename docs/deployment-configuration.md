# 단일 프로젝트의 기동과 설정

현재060의 배포 정본은 **하나의 컨테이너, `python app.py`, 한 프로세스**다. lifespan이 API, 공통 Agent Worker, Executor 이벤트 ingress/routing, reconciler, SSO와 공용 자원을 소유한다. 모든 graph 호출은 같은 총한도를 사용한다. 이벤트 수신 standalone 진입점은 진단용이며 graph를 실행하지 않는다. [공통 명령 Worker·DB 이행](agent-command-worker.md)을 따른다.

061에서 Worker와 SSE 알림을 DB 정본당 LISTEN 연결1개로 공유한다. 유휴 Worker가 유지하는 연결도 DB 예산에 포함하며 실효 summary는 `notification_listener`로 표시한다. config/수명/migration은 [알림 안내](agent-command-wakeup.md)를 따른다.

## 설정 원천과 공통 값

`service_settings.load_settings()`가 한 번 읽어 불변 snapshot을 만든다. API·Agent·이벤트·SSO typed 설정은 이 snapshot의 소비용 view다.

우선순위는 `config.{dev|stg|prd}.yml > config.yml > env > 기본값`이다. `--config`/`SERVICE_CONFIG_FILE`은 선택 파일 하나만 사용한다. 로컬 `--local-env-file`은 dev에서만 허용하며 프로세스 env보다 낮은 순위다. 명시한 YAML 값은 항상 env보다 우선한다. 환경변수로 조정할 값은 YAML에서 생략해야 한다.

| 공통 입력 | 사용하는 영역 | 예외 / 파생 규칙 |
|---|---|---|
| `DATABASE_URL` | API CRUD·Run 큐·프로젝트 메모리 Store | 이벤트 DB 미설정 시 이 주소의 psycopg DSN을 파생 |
| `EW_DATABASE_URL` | 이벤트 Inbox·binding 및 제출 bridge | 실행 활성 상태에서 DATABASE_URL과 같은 DB 정본 필수. 실제 자료 이행은 별도 |
| `WORKFLOW_DATABASE_URL` | Agent 추천 catalog | 미설정 시 이벤트 DB에서 파생. 기존 별도 DB는 명시 유지 |
| `CHECKPOINT_DB_URI` | Run 및 내장 이벤트 그래프의 공용 checkpointer | `AGENT_CHECKPOINT_DATABASE_URL`은 같은 값만 허용하는 구 별칭 |
| `REDIS_URL` | SSO 로그인 세션과 Executor Streams | 연결풀·key/group은 용도별로 분리. `EW_REDIS_URL`은 구 별칭 |
| `EXECUTOR_BASE_URL` | 실행 제출·결과 조회·이벤트 이력 보충 | `EW_EXECUTOR_BASE_URL`은 구 별칭 |
| `EXECUTOR_EVENTS_PATH` | 이벤트 이력 보충 경로 | 생략 시 `EXECUTOR_EXECUTION_PATH` + `/events`에서 유도. `EW_EXECUTOR_EVENTS_PATH`는 구 별칭 |
| `MODEL_*`, `API_BASE_URL` | 모든 Agent 역할의 기본 모델 설정 | 역할별 Agent 선언/프롬프트 구조는 유지 |
| `PHOENIX_ENDPOINT/PROJECT_NAME/API_KEY` | 프로세스 공용 관측 설정 | 구 `PHOENIX_CONFIG_PATH` 자동 탐색 없음 |
| `EW_NAMESPACE` | 이벤트 DB/Redis group 기본 이름 | stream/group은 namespace에서 파생, Executor 원본 stream은 별도 계약 |

084에서 사용자용 Jupyter registry·Redis ping API를 제거했다. `JUPYTER_ALLOWED_HOSTS`, `JUPYTER_HEALTH_TIMEOUT_SECONDS`, `JUPYTER_TOKEN_ENCRYPTION_KEY`, `REDIS_PING_TIMEOUT_SECONDS`, 미사용 `REDIS_HOST`는 삭제된 설정이며 YAML/env에 남으면 기동 설정 오류다. Redis 주소는 `REDIS_URL`이다. 기존 Executor·SSO·이벤트 설정과 `/service/*`는 유지한다. [설정·기존 DB 이행 안내](infrastructure-api-cleanup.md)를 따른다.

`EW_INSTANCE_ID`를 생략하면 startup UUID를 사용한다. 기존 .env에 고정 값이 남아 있어도 그 값은 prefix로만 취급하고 UUID를 추가한다. 같은 consumer ID가 여러 Pod/기동에 복제되지 않는다.

구 주소 별칭과 정본을 같은 소스에 서로 다른 값으로 주입하면 **오류로 중단**한다. YAML의 구 별칭은 정본으로 정규화한 뒤 env를 덮어쓴다. 따라서 YAML 우선순위는 유지된다. 새 예제는 정본 이름만 사용한다.

**DB 주소를 합치는 것과 연결풀 공유는 다르다.** CRUD는 asyncpg/SQLAlchemy, checkpoint·Store·이벤트는 서로 다른 psycopg 수명을 사용하므로 하나의 pool 객체로 억지로 합치지 않는다. 기존 DB 자료를 자동 이전하지 않는다. 현재060의 공통 원장·Inbox는 같은 DB 정본을 요구하며 checkpoint·Workflow는 별도 대상일 수 있다.

## 실행 옵션과 예산

환경 파일 예제는 Worker 활성/비활성을 가리지 않는다. 기본적으로 Run Worker가 켜져 있고, `EVENT_WORKER_ENABLED`가 없으면 Run Worker 활성 여부를 따른다. 명시한 `EVENT_WORKER_ENABLED`는 독립 적용된다. API만 필요하면 세 Worker flag를 모두 false로 지정한다. 실제 Executor 제출은 별도 `EXECUTOR_SUBMIT_ENABLED`이며 기본 false다. 모델 mock 여부로 제출이 자동 활성화되지 않는다.

```yaml
service:
  runtime:
    agent_worker_enabled: true
    task_reconciler_enabled: true
    event_worker_enabled: true
    agent_worker_concurrency: 4
  events:
    ew_ingress_concurrency: 4
    ew_pool_size: 4
```

위 설정의 graph 총한도는 **사용자 요청·승인·Executor 결과를 합쳐 최대4**다. ingress4는 원본 이벤트 수신/routing 병렬성으로 graph 실행 자리가 아니다. 기본 graph 한도는1이고 측정 profile은32다. 이전32+Event4의 측정치는 현재32와 총용량이 다르므로 재측정이 필요하다. 프로세스/Pod가 늘면 pool과 실행 한도도 복제된다.

`EW_DISPATCH_CONCURRENCY`, `EW_COMMAND_STREAM_NAME`, `EW_COMMAND_GROUP_NAME`, `EW_PUBLISH_LEASE_SECONDS`는 삭제했다. YAML/env에 남으면 명시적 오류이므로 제거한다. `EW_CONCURRENCY`는 `EW_INGRESS_CONCURRENCY`의 구 별칭으로만 받으며 별도 한도를 만들지 않는다.

`--check-config`는 실제 서버 포트·한도·활성 flag·설정 출처·pool 최대치를 자격증명 없이 출력하며 외부에 연결하지 않는다. pool 값은 열린 연결 수가 아니다. 기본 persistent pool 상한의 설정 합은 CRUD20 + checkpoint4 + 제출 bridge8 + 이벤트8 + Store2 + SSE listener1 = 43이다. 꺼진 기능과 lazy pool은 모두 열리지 않을 수 있다. Workflow의 일시 연결, migration과 다른 서비스는 이 합에 포함하지 않는다. 측정용 profile은 CRUD10 +4+4+4+2+1=25다. replica 전체 DB 상한은 플랫폼 replica 수와 DB 연결 예산을 함께 봐야 한다.

## 배포와 port

| 환경 | 기동 | 호스트 / 컨테이너 port |
|---|---|---|
| 기본 로컬 Compose | `python3 scripts/local.py up` | 기본18000 / 8000 |
| 외부 인프라 Compose | `docker compose -f compose.external.yaml up --build` | 8000 / 8000 |
| 기존 부하테스트 Compose | `docker compose -f compose.loadtest.yaml up --build` | 기본18080 / 8000 |
| 사내 CICD manifest | `python app.py`, `APP_ENV=dev`, `SERVER_PORT=5000` | Service5000 / 5000 |
| 범용 Kubernetes 예제 | `python app.py`, `APP_ENV=prd`, `SERVER_PORT=8000` | Service8000 / 8000 |

플랫폼이 공급하는 Gaia core는 수정하지 않는다. 플랫폼 앱을 쓰는 경우 원래 app/lifespan 조립 이후 서비스 lifespan을 결합해야 한다. 이번 검증은 저장소 app.py이며 사내 원본 Gaia core와의 실제 결합 검증은 아니다.

`deploy/dtest-agent.yaml`과 CICD는 한 컨테이너만 선언하며 init container와 별도 Worker Deployment/sidecar는 없다. CICD 이미지·replica·자원/HPA의 `[설정 값 변경 불가]` 값은 그대로 보존한다. 이를 실제로 치환하는 주체는 플랫폼이다. Ingress는 networking.k8s.io/v1이다.

`/service/ready`는 background loop, 실제 내장 이벤트 consumer·이벤트 DB/Redis, API tasks/agent_commands와 기존 pending/running invocation·READY 이벤트의 누락된 공통 명령을 확인한다. 기동 중 consumer가 아직 없거나 중단되면 503이다. DB·Redis 일시 장애는 준비 상태를 실패시키고 `/service/live` 자체는 생존을 유지한다. background loop 종료나 실행 소유권 불확실성은 live도 실패시킨다. API만 띄운 모드는 loop 검사이며 모든 CRUD 테이블·모델·Executor API 연결을 종합 검사하는 endpoint는 아니다. checkpoint/Store schema는 사전 migration 대상이다.

기존 이벤트 Worker metrics는 `/service/metrics`에서 process/default registry와 함께 노출한다. 별도8011 HTTP listener를 열지 않는다. 플랫폼의 기존 `/metrics` 경로는 덮어쓰지 않는다. Prometheus annotation도 공통 endpoint를 가리킨다.

SIGTERM을 받은 app.py가 먼저 신규 claim·SSE를 중지하고 drain을 시작한다. 기본 drain20초 + 종료관찰25초에 여유를 더해 manifest/Compose의 종료 유예는70초다. 자원 close와 플랫폼 lifespan도 시간이 필요하므로 두 설정을 크게 올리면 유예도 같이 늘려야 한다. 무조건30초 sleep하는 preStop은 제거했다.

## schema 준비와 기존 환경 전환

실행 전에, 서버와 **동일한 환경변수/선택 YAML**로 아래 schema를 준비한다. 애플리케이션 시작마다 reset하거나 migration하지 않는다. 일반 배포에서는 schema 준비를 직렬화한 사전 배포 단계에서 실행해야 한다. 이번 작업은 사내 CI stage를 추가하거나 자동 DB 이전을 하지 않았다.

```bash
PYTHONPATH=src python -m alembic -c alembic.crud.ini upgrade head
PYTHONPATH=src python -m alembic -c alembic.ini upgrade head
```

CRUD migration은 프로젝트 메모리 Store schema도 관리한다. checkpoint 초기화는 중앙 설정을 사용한다.

```python
# 저장소 루트, 서버와 같은 APP_ENV/SERVICE_CONFIG_FILE/env에서 1회 실행.
import asyncio
from service_settings import get_settings
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

async def prepare():
    async with AsyncPostgresSaver.from_conn_string(get_settings().agent.checkpoint_db_uri) as saver:
        await saver.setup()

asyncio.run(prepare())
```

로컬 helper는 `postgres` 호스트의 허용 DB만 대상으로 migration한다. `.env`는 수정하지 않고, 구 별칭을 정본으로 합친 `.env.local` 하나에 인프라 값·DB host override를 생성한다. 같은 파일을 Compose interpolation과 env_file로 사용한다. 직접 편집할 서비스 원천은 `.env`이며 로컬 port/관리 계정은 `.env.local`에서 보존한다. 원래 `.env`의 역할별 DB 이름·계정·URL 옵션은 유지한다.

기존 별도 Worker가 있는 환경에서는 **이전 Worker를 먼저 drain/종료**한 뒤 migration·단일 API 기동을 해야 한다. 로컬 helper는 자기 Compose 프로젝트의 `event-worker` label만 찾아70초 stop 후 제거한다. 다른 프로젝트의 Executor·DB·Redis는 대상이 아니다. 로컬 PostgreSQL named volume은 유지한다. Kubernetes도 구 Worker Deployment를 계속 띄운 채 새 내장 Worker를 롤아웃하는 방식으로 전환하지 않는다.

`compose.loadtest.yaml`의 기존 HTTP mock은 접수 fixture이며 실행 완료 이벤트까지 생성하지 않는다. 기존 Locust의 user/auth 시나리오도 현재 SSO 계약과 별도 갱신이 필요하다. 이 Compose의 기동 성공을 완료까지의 E2E/성능 검증으로 해석하지 않는다. 최신 완료 연계 fixture와 측정은 [Executor 처리량 검증](../scripts/benchmarks/executor_throughput/README.md)을 사용한다.

## 의존성

pyproject.toml + uv.lock을 정본으로 사용한다. dotenv는 직접 의존성으로 명시했다. root Dockerfile은 frozen uv sync, 사내 Dockerfile은 lock에서 생성한 requirements를 빌드 시 설치한다. startup에 패키지를 설치하지 않는다.

```bash
uv export --frozen --no-dev --no-emit-project --no-hashes -o requirements.txt
```

requirements를 독립 수정하지 않는다. 기존 lock 버전은 유지했지만 구 requirements의 별도 목록은 현재 lock 기준으로 교체했다. 사내 base는 보존했으며 Python3.11이 필요하다. 폐쇄망에서는 같은 lock의 wheel/패키지를 사내 인덱스 또는 wheelhouse로 제공해야 한다. 사내 base·SDK·망·PV·CI의 실제 실행은 별도 배포 검증 대상이다.

## Executor 이력 경로와 배포 버전 경계

이력 조회도 제출/결과 조회와 같은 `EXECUTOR_BASE_URL`을 사용한다. 기본 root 주소
`http://executor:8080`에서는 `/api/v1/executions/{execution_id}/events`를 호출한다.
base에 `/api/v1`을 포함한다면 제출/조회 PATH도 `/executions...`로 지정한다.
Worker는 `EXECUTOR_EXECUTION_PATH` 뒤에 `/events`를 붙이며 프록시에서 이력 경로만
다르면 `EXECUTOR_EVENTS_PATH`를 명시한다. prefix를 자동 추측하거나 두 번 붙이지 않는다.

066의 observations 증가분 쓰기는 새 reader가 기존 full list checkpoint와 pending write를
읽을 수 있다. 반대 방향은 호환되지 않는다. 구버전 LastValue reader가 새 tagged pending
write를 읽으면 list 대신 dict를 받는다. 기존 writer와 새 writer가 같은 실행 checkpoint를
번갈아 점유하는 혼합 배포 및 즉시 rollback은 검증된 방식이 아니다. 동시 writer 금지만으로
이 순차 교대 문제를 해결하지 못한다. 버전별 실행 고정 또는 전체 writer drain 후 전환,
rollback 전 pending write 정리/이행 등 배포 계약이 필요하며 현재 자동 보호는 미구현이다.
