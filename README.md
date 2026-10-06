# dtest-agent

설정 파일의 정본은 [YAML 앱 설정 안내](docs/application-configuration.md), 배포 구조는 [단일 프로젝트 배포 안내](docs/deployment-configuration.md)다. `app.py` 한 프로세스 안에서 API·Agent·이벤트 수신을 함께 실행한다. [058 검증 기록](docs/improvements/058-deployment-config-unification.md)을 참고한다.
dtest 프로젝트 공유

현재 리팩토링 작업 저장소는 [KimHwaJin/prj_re](https://github.com/KimHwaJin/prj_re)이고, 기준 브랜치는 `feature/refactor-base`다. [저장소·브랜치 작업 안내](docs/repository-workflow.md)를 따라 베이스에서 파생 브랜치를 만들고 작업한다. 이전 단계별 브랜치는 이력 확인용으로 보존한다.

전체 구조·설정·개발 경계는 [DTEST 서비스 구조](docs/architecture/service-layout.md), 이번 이동 추적은 [102 구조 정리](docs/improvements/102-dtest-service-structure.md)를 참고한다. API 구현 위치와 삭제·이동 내역은 [API 서비스 구조](docs/api-service-layout.md), DB 적용은 [마이그레이션 안내](docs/database_migrations.md)를 따른다.

## 리팩토링 브랜치의 현재 실행 계약

분석 Agent는 `src/dtest/agent_service/agents/analysis/`와 역할별 `agent_builders/<role>/`에 있다. 공개 API는 계획 제안·HITL·실제 Executor 실행·결과 판단·MULTI 수정·실행 전 재작성·완료 분석 후속 답변을 지원한다. [Agent API 요청과 응답](docs/public-run-api.md), [Workflow JSON 작성 규격](docs/workflow-json-reference.md), [검증된 JSON 예제와 schema](docs/contracts/agent-api/README.md)를 현재 연계 계약으로 참고한다. 루트 app.py, 중앙 설정과 비동기 자원 수명을 유지한다. Workflow CRUD의 공개2.0·다중 쿼리 HNSW 추천은 [등록·검색 계약](docs/workflow-registration-and-search.md)으로 구현했다. 실제 임베딩 모델 검증, 동적 Dataset Registry, Gaia adapter는 후속이다. 프로젝트 메모리는 공식 LangGraph Store·설정 가능한 입력/저장 예산·선택적 auto_context 정책을 구현했다. [Agent 개발 안내](docs/agent-development/README.md)는 현재 다섯 역할과 실행 경로를 설명한다.

Workflow 작업 영역은 [src/dtest/agent_service/agents/analysis/workflow/](src/dtest/agent_service/agents/analysis/workflow/README.md)다. 기존 skills·tools·workflows 하위 구조와 생성 스크립트를 보존하면서 분석의 Workflow 처리 코드와 한 패키지로 합쳤다.

현재 사용자용 API는 SSO 확인 후 발급하는 쿠키 로그인 세션으로 호출자를 식별한다. `X-User-Id`만 보내는 요청은 인증되지 않는다. 최초 직원은 일반 사용자와 기본 프로젝트로 자동 등록하며 역할·소유권·내부 UUID는 DB에서 관리한다. 사내 SDK 소스는 포함하지 않았고 폐쇄망에서 연결 함수 두 곳을 구현해야 실제 SSO 로그인이 가능하다. [SSO 적용·Swagger 테스트 가이드](docs/sso-authentication.md), [사용자 API·전환 가이드](docs/user-identity-api.md), [기동·설정 가이드](docs/configuration-bootstrap.md), [단계별 작업 기록](docs/improvements/README.md)을 참고한다.

사용하지 않는 `/api/v1/jupyter-servers*`, `/api/v1/redis/ping` 관리 API는 제거했다. 실제 Jupyter 실행은 Executor를 통하며 SSO·Streams의 Redis 사용은 유지한다. [현재 패키지·DB 정리 안내](docs/api-service-layout.md)를 따른다.

새 기능 확인 화면은 [독립 HTML 테스트 콘솔](tools/test-console/README.md)이다. 파일을 직접 열어 샘플을 보거나 같은 origin 진단 도구로 현재 API·SSE·HITL·관리 기능을 테스트한다. 기존 demo를 재사용하지 않으며 실제 모델/사내 SSO 여부와 검증 경계는 해당 안내를 따른다.

실제 모델의 자동 입력·HITL 편집·결과 기반 실행·후속 보고서는 [088 검증 기록](docs/reports/real-model-parameters-2026-10-04/README.md)에 실패 사례와 수정·재검증을 함께 남겼다. 이 기록을 고정 모델 테스트 화면이나 서비스 처리량 개선 결과와 구분한다.

아래의 기존 Docker·Locust 안내는 이전 실행 환경 기록이다. 헤더로 사용자를 선택하는 과거 부하테스트·진단 클라이언트는 현재 SSO API와 그대로 호환되지 않으며 쿠키·CSRF 세션 입력으로 이관해야 한다. 현재 내부 데모와 Swagger는 쿠키·CSRF를 사용한다. 기존 실행 컨테이너는 변경하지 않았다.

## 로컬 Docker 개발 환경

API는 선택한 YAML 하나를 읽고 DB URL의 host만 로컬 PostgreSQL 컨테이너로 바꾼 설정을 마운트한다. `.env.local`은 인프라 포트·비밀번호용이다. 로컬 config.yml을 먼저 준비한다.

```bash
uv run python scripts/local.py up --env local
```

테스트 화면: `http://127.0.0.1:18000/demo`

코드 수정 후 `uv run python scripts/local.py update --env local`로 다시 빌드·반영한다.
DB 데이터는 유지된다. 상세 설정과 로그 확인은 [로컬 Docker 가이드](docs/local-docker.md)를 참고한다.

## LLM 없는 서비스 부하테스트

LLM 응답을 고정하고 프로젝트·세션 생성 또는 Executor 제출까지 반복 시험한다.
`python3 scripts/loadtest/control.py --executor mock --build`로 기동한다. 실제 Executor 호출은 `--executor real`로 전환한다.
API 포트는 `18080`, Locust UI는 `18089`이며 `crud` / `submit` / `approval` 시나리오를 선택한다.
HTTP 시나리오 실행기와 Locust 파일, 지표 해석은 [서비스 부하테스트 가이드](docs/service-loadtest.md)를 참고한다.

PostgreSQL 서버는 pgvector>=0.8.0이 필요합니다. 기존 PostgreSQL17 볼륨을 유지할 때 서버 extension 설치가 가능한 이미지를 사용하고 실행 중 쓰기를 정리한 뒤 migration을 적용합니다. 설정 미확정 상태에서 임의 모델/차원으로 index를 생성하지 않습니다.

## CRUD API와 테스트 화면

서비스의 `/demo`는 현재 API·SSE·HITL·관리 기능을 확인하는 새 HTML 콘솔이다. API·Agent Worker·Executor 이벤트 수신과 같은 `app.py` 프로세스·중앙 설정을 사용한다. 별도 프론트 빌드·HTML 서버·임시 DB는 필요 없다.

```bash
uv sync --frozen
uv run python scripts/configure.py init --env local
# 생성한 config.yml의 실제 연결값을 수정하고 DB·계정을 준비
uv run python scripts/migrate.py
uv run python app.py
```

기본 접속 주소는 `http://127.0.0.1:8000/demo`다. 로컬 연결·정책·SSO는 config.yml 하나에서 설정하며 `.env`는 필요 없다. 사내 SSO adapter가 없는 일반 서비스는 로그인503이며 테스트 로그인으로 자동 전환하지 않는다. [클론 후 로컬 설정·schema·화면 실행](docs/service-demo-console.md), [격리 진단 실행 방법](tools/test-console/README.md)을 참고한다.

## 현재 Agent 개발 확인

```bash
python cli.py --request "데이터 품질 분석 계획을 제안해줘"
PYTHONPATH=src python -m dtest.devtools.analysis.visualization --output /tmp/analysis-current.mmd
```

CLI와 `langgraph dev`는 같은 현재 builder의 offline mock 계획·승인 확인이다. `.env`·실제 LLM·Executor·서비스 DB·Redis·SSO를 사용하지 않는다. 실제 Runs/Redis 완료 연계는 `python app.py`로 실행하는 서비스와 [API 계약](docs/public-run-api.md)을 따른다. `langgraph dev`와 이벤트 Worker를 함께 띄운다고 서비스 연계가 되는 구조로 안내하지 않는다.

현재 패키지 경계와 Agent 개발 위치는 [서비스 구조](docs/architecture/service-layout.md), Run 접수·실행·취소와 DB 수명은 [실행 인수인계](docs/run-execution-architecture.md)를 참고한다.
