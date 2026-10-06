# 로컬 PostgreSQL과 단일 애플리케이션

`uv run python scripts/local.py up --env dev` 또는 update로 API·Agent·Executor 이벤트 수신을 한 컨테이너의 app.py 한 프로세스에서 실행한다. 기본 화면은 http://127.0.0.1:18000/demo, Swagger는 /docs, 컨테이너 내부 port8000이다.

## 설정과 초기화

[앱 설정 안내](application-configuration.md)의 config.yml+config.dev.yml이 원천이다. helper는 같은 loader로 읽고 PostgreSQL host/port만 postgres:5432로 바꿔 workspace/config.compose.yml에 저장한다. DB 이름·계정·옵션과 Redis·LLM·Executor 값은 유지한다. 원본 .env나 profile을 수정하지 않는다. `.env.local`에는 Compose 인프라 값만 남긴다. 이전 LOCAL_API_PORT/LOCAL_POSTGRES_PORT/LOCAL_POSTGRES_PASSWORD 등은 유지한다. 원본 profile과 .env.local은0600이고 Git·이미지에서 제외한다. 컨테이너에 마운트하는 생성 YAML만0640으로 저장하고 해당 파일 GID를 보조 그룹으로 전달하여 비루트 프로세스가 읽도록 한다.

```sh
uv sync --frozen
uv run python scripts/configure.py init --env dev
# 실제 config.dev.yml 수정. 기존 .env는 init 대신 import-env로 이전 가능
uv run python scripts/local.py init --env dev
uv run python scripts/local.py up --env dev
uv run python scripts/local.py update --env dev
uv run python scripts/local.py status --env dev
uv run python scripts/local.py smoke --env dev
```

up/update는 이미지 빌드 후 기존 API와 같은 로컬 프로젝트의 구 event-worker를 drain/종료하고 migration 뒤 API를 재생성한다. named volume은 보존한다. DB 초기화는 chat_app·agent만 허용하고 선택한 역할을 로컬에 준비한다. DB 이름을 다른 이름으로 설정한 경우 자동 변경하지 않으며 bootstrap에서 거부한다. 실제 Executor 장기 대기 Run은 업데이트 후 재개 여부를 확인한다.

| 대상 | 호스트 | 컨테이너 내부 |
|---|---|---|
| API / Swagger / demo | 127.0.0.1:18000 | api:8000 |
| PostgreSQL | 127.0.0.1:15432 | postgres:5432 |
| Redis | 선택 YAML의 redis_url | 선택 주소 그대로 |

LOCAL_API_PORT/LOCAL_POSTGRES_PORT는 호스트 port다. 실제 Executor 공유 input/result root는 같은 절대 경로로 설정하고 존재하는 폴더를 마운트한다. 두 root가 다르면 별도 bind mount 설계가 필요하므로 helper가 거부한다. host에서만 유효한 127.0.0.1 Redis/Executor/모델 주소는 컨테이너에서 자기 자신을 의미하므로 실제 도달 가능한 주소로 바꾼다. Docker Desktop의 host 서비스는 host.docker.internal을 사용할 수 있다.

Redis는 local-redis 선택 profile이다. YAML에 redis://redis:6379/0을 넣으면 helper가 해당 profile로 기동한다. 외부 주소면 외부 Redis를 사용한다. Executor와 Redis·event Stream·consumer namespace를 맞춘다. 외부 환경과 같은 consumer group을 쓰는 경우 외부 이벤트도 소비할 수 있으므로 연계 대상에 맞춰 선택한다.

CRUD·Store·명령 원장·이벤트 Inbox는 DATABASE_URL DB를 공유한다. checkpoint는 CHECKPOINT_DB_URI DB다. 과거 EW_DATABASE_URL=agent로 분리했다면 새 실행에서는 같은 API DB 규칙을 만족해야 하며 기존 데이터는 자동 이전하지 않는다.

graph 동시성은 AGENT_WORKER_CONCURRENCY 하나로 설정한다. EW_INGRESS_CONCURRENCY는 이벤트 수신/routing 병렬성이다. 폐기된 LOCAL_API_WORKERS, LOCAL_EVENT_WORKER_PORT, EW_DISPATCH_CONCURRENCY는 사용하지 않는다. 한 세션 잠금은 유지한다.

smoke는 health/OpenAPI/readiness·DB identity·Redis를 확인한다. 실제 사용자 호출은 일반 SSO cookie/CSRF를 사용한다. SDK adapter 미설정이면 로그인503이며 test 계정 우회가 없다. 기존 PostgreSQL volume의 비밀번호를 모르거나 이전 .env.local이 있다면 LOCAL_POSTGRES_PASSWORD를 임의 교체하지 않는다. 실제 컨테이너 업데이트는 명시적으로 up/update를 실행할 때만 수행한다.
