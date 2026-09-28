# dtest-agent
dtest 프로젝트 공유

## 리팩토링 브랜치의 현재 실행 계약

분석 Agent 구현은 `src/agent_service/agents/analysis/`로 이동했다. [Agent 개발·이관 안내](docs/agent-development/README.md)와 [서비스 구조 및 이행 상태](docs/architecture/service-layout.md)를 먼저 참고한다. 루트 `app.py` 실행은 유지하며 006에서 Agent·LLM 호출을 비동기로 전환했다. 008에서 역할별 선언과 독립 프롬프트를 `agent_builders/<role>/`에 배치했다. create_agent·미들웨어 통일은 후속이다. 공통 API/실행기의 패키지 분리와 HTTP·DB·파일 I/O 전체 전환은 후속 단계다.

기존 Workflow 작업 영역은 [src/app/workflow/](src/app/workflow/README.md)에 유지한다. Skill·Executor Tool·Workflow 정책과 생성 스크립트는 기존 위치에서 수정하고, 새 Agent는 이 패키지를 참조한다.

현재 사용자용 API는 `X-User-Id` 문자열 헤더를 사용하며 Bearer UUID 및 공개 사용자 가입/이름 조회 방식은 제거했다. 먼저 DB 마이그레이션과 최초 관리자 초기화를 수행한다. [사용자 API·전환 가이드](docs/user-identity-api.md), [기동·설정 가이드](docs/configuration-bootstrap.md), [단계별 작업 기록](docs/improvements/README.md)을 현재 계약으로 참고한다.

아래의 기존 Docker·데모·Locust 안내는 이전 실행 환경 기록이다. 해당 클라이언트의 Bearer 및 자동 가입 흐름은 새 사용자 계약으로 아직 이관하지 않았으므로 이 브랜치에서 그대로 호환된다고 보지 않는다. 기존 실행 컨테이너는 변경하지 않았다.

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
PYTHONPATH=src ../.venv311/bin/python -m app.agent_worker.worker_main
```
