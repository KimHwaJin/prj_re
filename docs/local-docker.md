# 로컬 PostgreSQL + .env 기반 Docker 환경

API는 저장소 `.env`를 읽는다. PostgreSQL URL의 host만 Docker 내부 주소 `postgres`로 바꾼다. DB 이름·계정·비밀번호·URL 옵션과 Redis·모델·Executor 설정은 `.env`를 유지한다. 컨테이너 안에서 `localhost`는 API 자신이므로 DB에는 `postgres:5432`로 연결한다.

## 기동과 업데이트

```bash
python3 scripts/local.py up
python3 scripts/local.py update
```

`up`과 `update`는 `.env`로부터 DB host override를 `.env.local`에 다시 생성하고, 이미지를 빌드하여 로컬 DB migration을 적용한 후 API를 재생성한다. `update`는 migration 전에 기존 API를 중지한다. 데이터는 named volume에 보존된다. 실행 중인 Run이 끝나거나 HITL 대기 상태일 때 업데이트한다.

`.env` 원본은 수정하지 않는다. `.env.local`은 비공개 생성 파일이며 애플리케이션의 env_file로 사용하지 않는다. 호스트 포트와 기존 로컬 관리 계정 비밀번호는 이 파일에서 보존한다. 모델 등 나머지 설정은 매번 `.env`에서 직접 주입한다. 의존성은 `uv.lock`을 그대로 사용한다.

## 접속

API 프로세스 수는 `.env.local`의 `LOCAL_API_WORKERS`로 설정하며 기본은 `4`다. 각 프로세스는 자체 Run Worker와 Task reconciler를 시작한다(`.env`의 활성화 설정에 따름). API 컨테이너 하나 안에서 같은 PostgreSQL Run 큐·checkpoint DB를 공유하며, 접속 주소는 계속 18000 포트 하나다. Executor 이벤트 Worker 수와는 별개다. 값을 바꾼 뒤 `python3 scripts/local.py update`를 실행하면 반영된다. 프로세스 수만큼 DB connection pool도 늘어나므로 많은 수로 늘릴 때는 PostgreSQL 연결 한도를 함께 확인한다.

| 대상 | 주소 |
|---|---|
| Demo | http://127.0.0.1:18000/demo (사용자 `local-dev`) |
| Swagger | http://127.0.0.1:18000/docs |
| Health | http://127.0.0.1:18000/health |
| 이벤트 Worker readiness | http://127.0.0.1:18011/health/ready |
| PostgreSQL | `127.0.0.1:15432` |
| Docker 컨테이너에서 API 접근 | `http://host.docker.internal:18000` |

VSCode DB 연결은 Host `127.0.0.1`, Port `15432`, 사용자·비밀번호는 `.env`의 DB URL에 있는 값, Database는 `chat_app` 또는 `agent`를 입력한다. SSL은 비활성화한다. 기존 `dtest` 관리 계정과 `.env.local`의 `LOCAL_POSTGRES_PASSWORD`로도 계속 접속할 수 있다.

## DB 초기화와 설정

| 설정 | 현재 로컬 대상 | 초기화 |
|---|---|---|
| `DATABASE_URL` | `postgres:5432/chat_app` | `alembic.crud.ini upgrade head` |
| `CHECKPOINT_DB_URI`, `AGENT_CHECKPOINT_DATABASE_URL` | `postgres:5432/agent` | LangGraph `AsyncPostgresSaver.setup()` |
| `EW_DATABASE_URL` | `postgres:5432/agent` | `alembic.ini upgrade head` |
| `WORKFLOW_DATABASE_URL` | `.env` 값 또는 `EW_DATABASE_URL`의 로컬 주소 | Worker migration |

bootstrap은 로컬 host·DB를 검증하고 `.env`에 지정된 로그인 계정을 로컬 DB에 생성/갱신한다. 기존 테이블을 유지하기 위해 해당 계정에 로컬 소유자 역할 `dtest`를 부여한다. 외부 DB에는 migration을 실행하지 않는다.

Redis는 `.env`의 `REDIS_URL`·`EW_REDIS_URL`을 사용한다. 로컬 Redis 서비스는 선택적 `local-redis` 프로필로 남겨두며 API에서 자동으로 사용하지 않는다. Executor 제출 여부도 `.env`의 `EXECUTOR_SUBMIT_ENABLED`를 따른다.

`event-worker` 컨테이너 1개는 `python -m app.agent_worker.worker_main`으로 실행되어 Executor 이벤트를 수집하고 Agent를 재개한다. API와 같은 이미지·로컬 `agent` DB·checkpoint 설정을 사용한다. Executor 서버 자체를 추가로 기동하는 것은 아니다. API 컨테이너 안의 Run Worker 4개와 별개의 서비스다.

사용자 선택에 따라 Redis 소비 그룹·명령 스트림·instance ID도 `.env` 그대로 사용한다. 현재 설정의 `dtest-agent:ingress`와 `dtest-agent:dispatch`는 외부 Redis의 기존 그룹이며, DB는 로컬이므로 다른 환경의 Worker와 메시지 소비를 나누게 된다. 로컬 DB에 없는 command가 전달되면 `Unknown command ID`로 처리될 수 있다. 이 구성을 독립된 이벤트 처리 환경으로 간주하지 않는다.

Executor 공유 경로는 `.env`의 절대 경로를 같은 위치에 bind mount하여 PATH 설정을 유지한다. 현재 helper는 입력·결과 공유 경로가 같을 때 지원하며, 다르면 별도 mount 구성을 요구한다. 외부 Executor가 그 경로를 읽을 수 있는지는 실제 Executor 환경에 따라 별도로 검증해야 한다.

## 상태와 종료

```bash
python3 scripts/local.py status
python3 scripts/local.py logs
python3 scripts/local.py smoke
python3 scripts/local.py down
```

`smoke`는 API health, OpenAPI, 사용자 CRUD, 실제 DB·Redis 연결 및 이벤트 Worker readiness를 확인한다. 모델 요청이나 Executor 실행을 시작하지 않는다. `status`는 API와 이벤트 Worker의 실제 DB 이름·host, Redis host, revision, Executor 활성 여부를 표시한다. `logs`는 두 서비스의 로그를 함께 보여준다. `down`은 named volume을 삭제하지 않는다.

기본 `compose.yaml`은 `compose.local.yaml`을 include한다. 일상적인 업데이트에는 DB override 동기화·migration·검증을 포함한 helper를 사용한다. 외부 DB까지 사용하는 원래 구성은 `compose.external.yaml`에 보존했다.

## 검증 범위

최초 구축에서는 로컬 DB·Redis 및 Executor 비활성 설정으로 실제 모델의 데이터 선택 HITL → 이미지 업데이트 → 같은 세션의 분석 조건 HITL 재개까지 확인했다. 이후 사용자 요청에 따라 DB host만 로컬로 바꾸고 나머지를 `.env`와 일치시키는 구성으로 변경했다. 변경된 구성에서는 설정 일치, DB migration, API 및 DB·Redis 연결을 확인하며, 최초 HITL 검증을 현재 구성의 Executor 실행 검증으로 간주하지 않는다.
