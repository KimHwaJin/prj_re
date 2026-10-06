# dtest-agent

FastAPI API, LangGraph 분석 Agent와 background Worker를 루트 `app.py`의
한 프로세스에서 실행한다. 작업 저장소는
[KimHwaJin/prj_re](https://github.com/KimHwaJin/prj_re), 리팩토링 베이스는
`feature/refactor-base`다. [브랜치 작업 안내](docs/repository-workflow.md)를 따른다.

## 로컬 실행

```sh
uv sync --locked
uv run python scripts/configure.py init --env local
# 생성한 config.yml의 DB·Redis·모델·Executor·SSO 연결값 수정
uv run python app.py --env local --check-config
uv run python scripts/migrate.py --env local
uv run python app.py --env local
```

이미 config.yml을 작성했다면 init을 반복하지 않는다. 기본 주소는
`http://127.0.0.1:8000/demo`, Swagger는 `/docs`다. 앱·Worker·HTML이 같은
서비스 설정을 사용하며 별도 프론트 서버는 필요 없다.

[앱 설정](docs/application-configuration.md),
[기동과 자원 조립](docs/configuration-bootstrap.md),
[DB 준비](docs/database_migrations.md),
[기능 콘솔](docs/service-demo-console.md)을 따른다. PostgreSQL에는
pgvector >=0.8.0이 필요하다. DB_INIT_ON_START로 앱 시작 전 같은 migration을
실행할 수도 있으며 기본값은false다.

인증은 SSO 로그인 쿠키와 변경 요청의 CSRF다. X-User-Id나 Bearer 문자열만으로
인증하지 않는다. 최초 직원은 일반 사용자와 기본 프로젝트로 자동 등록한다.
사내 SDK 소스는 포함하지 않았으며 폐쇄망에서 adapter factory를 연결해야 한다.
미설정 로그인은503이고 자동 테스트 인증으로 전환하지 않는다.
[SSO·Swagger 안내](docs/sso-authentication.md)를 참고한다.

## 현재 개발·연계 계약

- [서비스 구조](docs/architecture/service-layout.md): API·Agent·Worker와
  application/contracts/infrastructure/settings의 책임.
- [Agent 개발 안내](docs/agent-development/README.md): 역할별 agent_builders,
  middleware, Skill·Tool·Workflow 등록 자산과 실행 경로.
- [Run API·SSE](docs/public-run-api.md): 통합 새 요청/재개, 상태·HITL·결과.
- [Workflow 공개 표준2.0](docs/workflow-standard.md)과
  [등록·검색](docs/workflow-registration-and-search.md): JSON·다중 쿼리·HNSW.
- [내부 계획 JSON](docs/workflow-json-reference.md): 공개 Workflow와 구분되는
  승인·실행 정의 및 필드 설명.
- [프로젝트 메모리](docs/project-memory.md): LangGraph Store·예산·갱신 정책.
- [문서 안내](docs/README.md): 현재 정본과 과거 설계·측정 이력 구분.

등록 Tool은 import되지 않더라도 Executor에 코드로 제출하는 실행 자산이다.
[workflow 패키지](src/dtest/agent_service/agents/analysis/workflow/README.md)의
skills/tools/workflows 구조와 가이드를 따른다. 예시 Tool 구성에 Agent를 고정하지
않는다. 동적 Dataset Registry와 실제 Gaia 템플릿 연결 검증은 후속이다.

## 회귀와 성능 검증

```sh
uv run python -m pytest --collect-only -q
uv run python -m pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked ty check
```

기본 pytest는 현재 회귀와 진단 도구 테스트만 수집한다. DB/Redis opt-in 조건이
없으면 통합 테스트가 skip되므로 수집 성공이나 unit 통과를 전체 연계 검증으로
해석하지 않는다. [테스트 안내](tests/README.md)를 따른다.

현재 사용 가능한 성능 도구와 범위는 [부하 검증 안내](docs/service-loadtest.md)를
따른다. `scripts/loadtest`의 옛 Locust 클라이언트는 SSO 인증과 현재 Executor
제출 시나리오로 이행되지 않았으므로 현재 서비스에 그대로 실행하지 않는다.
과거 측정 원본은 날짜·commit 기준의 이력이며 현재 처리량을 보장하지 않는다.

Docker를 쓰려면 [로컬 Compose 안내](docs/local-docker.md)를 따른다.
일반 로컬 app.py 실행 자체에 Docker가 필수인 것은 아니다.
