# dtest-agent
dtest 프로젝트 공유

현재 리팩토링 작업 저장소는 [KimHwaJin/prj_re](https://github.com/KimHwaJin/prj_re)이고, 기준 브랜치는 `feature/refactor-base`다. [저장소·브랜치 작업 안내](docs/repository-workflow.md)를 따라 베이스에서 파생 브랜치를 만들고 작업한다. 이전 단계별 브랜치는 이력 확인용으로 보존한다.

## 리팩토링 브랜치의 현재 실행 계약

분석 Agent 구현은 `src/agent_service/agents/analysis/`에 있고 역할별 선언·독립 prompt는 `agent_builders/<role>/`에 둔다. 공개 API는 038~039의 새 계획/실행 Graph를 사용한다. 통합 Run 접수·HITL·SSE에 이어 실제 Executor 제출·결과 판단·후속 Operation·리포트·Finalize를 연결했다. [공개 API](docs/public-run-api.md), [Executor Runtime](docs/agentic-executor-runtime.md), [039 작업·검증 결과](docs/improvements/039-agentic-executor-runtime.md)를 현재 계약으로 참고한다. 루트 `app.py`와 중앙 설정/비동기 자원 수명을 유지한다. 기존 CLI/Graph의 이행, 자동 수정 단계, Workflow 추천·관리, 데이터 catalog·프로젝트 메모리는 후속이다.

Workflow 작업 영역은 [src/agent_service/agents/analysis/workflow/](src/agent_service/agents/analysis/workflow/README.md)다. 기존 skills·tools·workflows 하위 구조와 생성 스크립트를 보존하면서 분석의 Workflow 처리 코드와 한 패키지로 합쳤다.

현재 사용자용 API는 SSO 확인 후 발급하는 쿠키 로그인 세션으로 호출자를 식별한다. `X-User-Id`만 보내는 요청은 인증되지 않는다. 최초 직원은 일반 사용자와 기본 프로젝트로 자동 등록하며 역할·소유권·내부 UUID는 DB에서 관리한다. 사내 SDK 소스는 포함하지 않았고 폐쇄망에서 연결 함수 두 곳을 구현해야 실제 SSO 로그인이 가능하다. [SSO 적용·Swagger 테스트 가이드](docs/sso-authentication.md), [사용자 API·전환 가이드](docs/user-identity-api.md), [기동·설정 가이드](docs/configuration-bootstrap.md), [단계별 작업 기록](docs/improvements/README.md)을 참고한다.

아래의 기존 Docker·Locust 안내는 이전 실행 환경 기록이다. 헤더로 사용자를 선택하는 과거 부하테스트·진단 클라이언트는 현재 SSO API와 그대로 호환되지 않으며 쿠키·CSRF 세션 입력으로 이관해야 한다. 현재 내부 데모와 Swagger는 쿠키·CSRF를 사용한다. 기존 실행 컨테이너는 변경하지 않았다.

## 로컬 Docker 개발 환경

API는 기존 `.env` 설정을 사용하되 DB URL의 host만 로컬 PostgreSQL 컨테이너로 바꾼다.
DB 이름·계정·비밀번호와 Redis·모델·Executor 설정은 `.env`를 따른다.

```bash
python3 scripts/local.py up
```

테스트 화면: `http://127.0.0.1:18000/demo` (사용자 `local-dev`)

코드 수정 후 `python3 scripts/local.py update`로 다시 빌드·반영한다.
DB 데이터는 유지된다. 상세 설정과 로그 확인은 [로컬 Docker 가이드](docs/local-docker.md)를 참고한다.

## LLM 없는 서비스 부하테스트

LLM 응답을 고정하고 프로젝트·세션 생성 또는 Executor 제출까지 반복 시험한다.
`python3 scripts/loadtest/control.py --executor mock --build`로 기동한다. 실제 Executor 호출은 `--executor real`로 전환한다.
API 포트는 `18080`, Locust UI는 `18089`이며 `crud` / `submit` / `approval` 시나리오를 선택한다.
HTTP 시나리오 실행기와 Locust 파일, 지표 해석은 [서비스 부하테스트 가이드](docs/service-loadtest.md)를 참고한다.

## CRUD API와 테스트 화면

LangGraph/노드 구현과 분리된 FastAPI 계층에서 사용자, 프로젝트, 세션, 메시지,
실행 이력을 PostgreSQL에 저장합니다. CRUD 스키마는 기존 Worker migration과 별도로
관리합니다.

```bash
uv sync
alembic -c alembic.crud.ini upgrade head
uv run dtest-agent-api
```

브라우저에서 `http://127.0.0.1:8000/demo`를 열면 CRUD와 Agent 실행 흐름을 시험할 수
있습니다. DB/Redis/checkpoint 연결은 `DATABASE_URL`, `REDIS_URL`,
`CHECKPOINT_DB_URI` 및 기존 `EW_*` 환경변수로 덮어쓸 수 있습니다.

## Local Redis event worker

Executor 제출 후 LangGraph run은 interrupt 상태로 종료됩니다. Redis event
worker가 Executor 완료 이벤트를 소비하고 같은 thread/checkpoint를 resume합니다.
로컬 테스트할 때는 migration이 적용된 PostgreSQL과 Redis가 필요하며,
두 터미널에서 각각 실행합니다.

```bash
langgraph dev
```

```bash
set -a
source .env
set +a
PYTHONPATH=src ../.venv311/bin/python -m api_service.agent_worker.worker_main
```

현재 패키지 경계와 Agent 개발 위치는 [서비스 구조](docs/architecture/service-layout.md)를 참고하세요.
