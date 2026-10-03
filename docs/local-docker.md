# 로컬 PostgreSQL과 단일 애플리케이션

`python3 scripts/local.py up` 또는 `update`로 API·Agent·Executor 이벤트 Worker를 **한 API 컨테이너의 app.py 한 프로세스**에서 실행한다. 기본 주소는 `http://127.0.0.1:18000`, 내부 port8000이다. 이제 별도 이벤트 Worker port18011은 배포 정본에서 사용하지 않는다.

서비스 설정의 원천은 `.env`다. helper가 구 별칭을 공통 이름으로 합치고 PostgreSQL host만 `postgres`로 바꿔 `.env.local` 하나에 생성한다. DB 이름·계정·옵션, Redis·모델·Executor 주소는 유지한다. `.env`는 수정하지 않는다. `.env.local`은 비공개 생성 파일이며 Compose interpolation과 애플리케이션 env_file 양쪽에 사용된다. 직접 편집한 LOCAL_API_PORT/LOCAL_POSTGRES_PORT/LOCAL_POSTGRES_PASSWORD는 보존된다.

```bash
python3 scripts/local.py init
python3 scripts/local.py up
python3 scripts/local.py update
python3 scripts/local.py status
python3 scripts/local.py smoke
```

up/update는 이미지 빌드 후 기존 API와 같은 로컬 Compose 프로젝트의 구 event-worker만 drain/종료하고, migration 뒤 API 하나를 재생성한다. PostgreSQL named volume은 보존한다. 기존 Worker를 정리하지 않은 단순 `docker compose up` 전환은 중복 이벤트 소비를 남길 수 있으므로 helper를 사용한다. 실제 Executor 실행 중에는 업데이트가 끝난 뒤 기존 대기 Run을 이어가는지 별도로 확인한다.

기본 연결:

| 대상 | 호스트 주소 | 컨테이너 내부 |
|---|---|---|
| API·Swagger·demo | 127.0.0.1:18000 | api:8000 |
| PostgreSQL | 127.0.0.1:15432 | postgres:5432 |
| Redis | `.env`의 REDIS_URL | 같은 공용 REDIS_URL |

Redis 컨테이너는 `local-redis` 선택 profile이다. `.env`에서 `redis://redis:6379/0`을 선택하면 helper가 이 profile로 Redis도 기동한다. 외부 Redis 주소를 선택하면 그대로 연결하고 로컬 Redis는 띄우지 않는다. 실제 Executor 이벤트는 Executor가 발행하는 Redis·stream과 서비스 수신 설정이 일치해야 한다.

CRUD/Store는 `.env`의 DATABASE_URL DB, checkpoint는 CHECKPOINT_DB_URI DB다. 이벤트 DB override가 없으면 DATABASE_URL에서 파생되고, Workflow override가 없으면 이벤트 DB에서 파생된다. 기존 .env에서 EW_DATABASE_URL=agent를 사용하면 계속 agent DB를 본다. SQL 초기화 파일은 신규 볼륨에 agent DB를 만들며 기존 자료를 이전하지 않는다.

LOCAL_API_WORKERS와 LOCAL_EVENT_WORKER_PORT는 더 이상 설정하지 않는다. 동시 처리는 AGENT_WORKER_CONCURRENCY/EW_DISPATCH_CONCURRENCY로 정한다. 기존4프로세스×Run C에서 총한도를 유지하려면1프로세스의 Run 한도를4C로 명시해 검증해야 한다. 자동으로 값을 곱하지 않는다. 한 session 잠금과 다른 session 독립 실행은 유지된다. 두 실행 한도를 하나로 통합하는 작업은 다음 단계다.

smoke는 health/OpenAPI/통합 readiness와 실제 DB identity·Redis를 확인한다. 테스트 사용자를 우회 등록하지 않는다. 현재 API는 SSO 로그인 cookie/CSRF를 따르므로 Swagger/demo의 실제 사용자 호출에는 사내 adapter 설정이 필요하다.

[전체 설정과 배포 안내](deployment-configuration.md)를 따른다. 기존 볼륨의 비밀번호를 모르면 새로 덮어쓰지 말고 현재 LOCAL_POSTGRES_PASSWORD를 유지한다.
