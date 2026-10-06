# 기동과 서비스 조립

루트 `app.py`는 `dtest.bootstrap.main()`을 호출한다. 중앙 설정은
`src/dtest/settings/loader.py`에서 확정하며 API·Agent·Worker가 같은
ServiceSettings snapshot을 사용한다. 파일 선택·설정 키의 정본은
[앱 설정](application-configuration.md), 패키지 경계는
[서비스 구조](architecture/service-layout.md)다.

## 로컬 실행

```sh
uv sync --locked
uv run python scripts/configure.py init --env local
# config.yml의 DB·Redis·LLM·Executor·SSO 연결값을 수정
uv run python app.py --env local --check-config
uv run python scripts/migrate.py --env local
uv run python app.py --env local
```

이미 작성한 config.yml이 있으면 init을 반복할 필요 없다. 기본 포트는8000이며
`PORT`로 바꾼다. `/demo`는 같은 앱의 기능 콘솔, `/docs`는 Swagger다.
SSO adapter가 없으면 실제 로그인은503이며 자동 테스트 인증으로 전환하지 않는다.
[로컬 화면 안내](service-demo-console.md), [SSO 안내](sso-authentication.md)를 따른다.

`--check-config`는 설정 선택·검증만 한다. DB 연결·테이블 존재·외부 서비스의
정상 응답을 확인하는 명령은 아니다.

## 설정 선택과 우선순위

| 실행 환경 | 선택 파일 |
| --- | --- |
| 기본 / local | config.yml |
| dev | config.dev.yml |
| stg | config.stg.yml |
| prd | config.prd.yml |

`--env` 또는 `APP_ENV`로 선택한다. `--config` / `SERVICE_CONFIG_FILE`로 파일을
명시할 수도 있다. 공통 YAML과 환경 YAML을 병합하지 않는다. 키별 우선순위는
**선택한 YAML > 프로세스 환경변수 > 코드 기본값**이다.

설정은 플랫폼과 같은 최상위 대문자 키 형식으로 작성한다.

```yaml
PORT: 8000
DB_INIT_ON_START: false
AGENT_WORKER_ENABLED: true
AGENT_WORKER_CONCURRENCY: 4
EVENT_WORKER_ENABLED: true
TASK_RECONCILER_ENABLED: true
```

YAML의 명시적 false/0은 유지한다. YAML에 값이 있으면 같은 환경변수로 덮어쓸 수
없다. 환경변수 제어가 필요한 키는 YAML에서 생략한다. 현재 예시는
[config.example.yml](../config.example.yml)을 참고한다. `.env`를 자동 탐색하거나
전역 os.environ을 수정하지 않는다. 명시적인 `--local-env-file`은 local/dev의
이전 dotenv 이행용이며 실제 앱 설정의 정본은 선택한 YAML이다.

## DB 준비

`DATABASE_URL`은 CRUD·명령 원장·Workflow·LangGraph 프로젝트 Store의 DB다.
Executor Inbox/binding의 `EW_DATABASE_URL`은 생략 시 같은 DB의 psycopg URI로
파생된다. 활성 실행 경로에서 서로 다른 DB 정본은 거절한다.
`CHECKPOINT_DB_URI`는 LangGraph checkpoint의 별도 DB일 수 있다.

`DB_INIT_ON_START` 기본값은false다. true이면 앱 lifespan에서 API·Worker를
시작하기 전에 CRUD Alembic → Event Alembic → checkpoint setup을 실행한다.
기존 DB에도 head migration이 적용되며 DB 서버나 database 자체는 생성하지 않는다.
YAML에 이 키가 없을 때 다음 환경변수로 제어할 수 있다.

```sh
DB_INIT_ON_START=true uv run python app.py --env local
```

수동 적용은 `scripts/migrate.py`가 같은 설정과 준비 코드를 사용한다.
DB·권한·pgvector 요구 사항과 Windows 실행은
[마이그레이션 안내](database_migrations.md)를 따른다. 접속 끊김 오류를
테이블 초기화 문제와 동일하게 취급하지 않는다.

## Worker와 자원 수명

공통 Agent Worker는 사용자 시작·승인·Executor 결과 명령을 같은 원장에서 실행한다.
`AGENT_WORKER_CONCURRENCY`는 프로세스 내 graph 호출의 합계 한도다.
이벤트 수신부는 Redis 이벤트를 Inbox/명령으로 연결하며 graph를 별도로 실행하지
않는다. Task reconciler도 별도 background loop다. API만 띄우려면 세 활성화
키를 false로 둔다. [공통 Worker](agent-command-worker.md)를 따른다.

모델/Executor 대기 중 서비스 DB transaction을 붙잡지 않는다. HITL/Executor
대기로 graph 호출이 반환되면 실행 자리를 반환한다. WAITING_EXECUTOR의 같은
세션 입력 잠금은 유지한다. API SQLAlchemy·checkpoint·Store·이벤트 및 HTTP
자원은 각 수명에서 재사용하며 하나의 물리적 풀로 합친다는 의미는 아니다.

`SHUTDOWN_DRAIN_SECONDS`는 정상 완료 유예, `SHUTDOWN_TIMEOUT_SECONDS`는
취소 후 정리 관찰 시간이다. 실행 종료가 확인되기 전에 자원을 먼저 닫지 않는다.
세부 한도는 [처리량 설정](service-throughput-settings.md),
[Executor HTTP](executor-http-runtime.md), [배포 안내](deployment-configuration.md)를
따른다. 플랫폼 앱에 붙일 때는 라우터·미들웨어·계측·lifespan의 조립 순서를
보존해야 하며 실제 Gaia 템플릿 연계 검증은 별도다.
