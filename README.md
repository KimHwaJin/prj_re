# dtest-agent
dtest 프로젝트 공유

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
