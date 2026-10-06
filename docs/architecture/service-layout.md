# DTEST 서비스 구조

102 구조 정리 기준. 우리 서비스의 설치 패키지는 `src/dtest` 하나다. API, Agent, Worker는 같은 배포에서 함께 실행하되 각 패키지의 책임을 분리한다. `python app.py`가 단일 FastAPI 앱과 두 종류의 background loop를 시작한다.

```text
app.py
src/dtest/
  bootstrap.py                     앱 생성·lifespan·시작/종료 순서
  container.py                     구체 구현·자원·포트를 조립
  lifecycle.py                     cancellation에 안전한 자원 종료
  settings/                        유일한 설정 로더와 책임별 불변 타입
    loader.py sources.py           YAML > env > 기본값, 키 검증
    models.py                      전체 설정 snapshot
    api.py agent.py worker.py      API·Agent·명령 실행 설정
    events.py                      EventWorkerSettings: Redis 이벤트 수신 설정
    database.py redis.py storage.py DB·Redis·Workflow 파일 저장 설정
    auth.py search.py runtime.py   SSO·추천·프로세스 수명 설정
  api_service/
    http/                          라우터·인증 dependency·HTTP 오류 변환
    auth/                          로그인·쿠키·CSRF·Swagger 연동
    middleware/ streaming.py       request ID·SSE HTTP 응답
    web/                           /demo 테스트 화면
  agent_service/
    factory.py context.py          LangChain 역할 Agent 생성·실행 맥락
    middleware/ runtime/           공통 middleware·모델·checkpoint 어댑터
    agents/analysis/
      planning/ execution/         LangGraph 계획·승인·실행·관찰·리포트
      agent_builders/<role>/       역할 선언과 역할별 프롬프트
      workflow/skills/             개발자가 유지할 Skill MD
      workflow/tools/              개발자가 유지할 실행 Python 함수
      workflow/workflows/          Workflow 작성 규약
  worker_service/
    command_worker.py              사용자 입력·Executor resume 공통 실행 슬롯
    reconciler.py                  stale 실행 상태 점검
    executor_events/               Redis 수신·순서 보장·명령 접수
  application/
    resources/                     사용자·프로젝트·세션·메시지 정책/조회
    runs/                          Run 접수·소유권·취소·결과 저장·로그
    workflows/                     Workflow CRUD·공개·색인 정책
    admin.py                       최초 관리자 등록
  contracts/                       API·Agent·Worker가 공유하는 DTO·enum·port
  infrastructure/
    database/                      ORM·repository·풀·알림·이벤트 원장
    redis/                         로그인 세션 저장
    memory/                        LangGraph Store·PG 문서 쓰기
    executor/                      고정 API 경로·HTTP·manifest·artifact adapter
    workflow_search/               임베딩·pgvector HNSW·추천 adapter
    file_storage/                  Workflow JSON 저장
    sso/ observability/            사내 SDK 경계·진단·Phoenix
  devtools/                        오프라인 개발 도구

tests/api_service/ tests/agent_service/
crud_migrations/ migrations/        기존 DB 이행 이력
config.yml                         로컬 설정
config.{dev,stg,prd}.yml            플랫폼 프로파일 설정
```

## 의존성과 연결

- HTTP 라우터는 입력 검증·인증·업무 함수 호출·응답을 담당한다. 목록 SQL과 Workflow 색인 정책은 `application`에 있다.
- `application`은 FastAPI와 LangChain/LangGraph를 직접 import하지 않는다. 공용 오류는 `ApplicationError`이고 HTTP 변환은 API에서 수행한다.
- Agent는 API·업무 서비스·Worker를 import하지 않는다. Executor, Store, Workflow 추천 등을 Runtime에 주입받는다.
- `contracts`는 서비스 구현이나 DB/프레임워크를 import하지 않는다. 모델 callback도 업무 코드가 아닌 Agent adapter가 담당한다.
- `container.py`가 Agent graph, checkpoint, Store, Executor 연결을 조립한다. `bootstrap.py`는 설치한 composition을 이용해 요청 처리와 background loop 수명을 관리한다.
- 이벤트 수신 Worker는 실행하지 않고 원본 이벤트 저장·순서 검사·공통 명령 접수까지만 담당한다. command Worker가 사용자와 Executor 입력을 같은 실행 한도에서 처리한다.
- 이벤트 DB Store는 Worker 구현이 아니라 `infrastructure/database/event_store.py`와 `binding_signals.py`다. Executor 제출 binding이 Worker 패키지를 역참조하지 않는다.

## 설정과 개발 규칙

설정은 `dtest.settings.loader.load_settings()` 한 곳에서만 읽는다. 소비자는 `get_settings().api`, `.agent`, `.commands`, `.worker`(이벤트 수신), `.database`, `.redis`, `.storage`, `.sso`, `.workflow_search`를 사용한다. 모듈 import 시 DB/Redis/LLM 연결을 열지 않는다.

외부 YAML의 기존 flat 키와 플랫폼 오타 alias 지원은 유지한다. 이 구조 변경 때문에 고객 설정 파일을 갈아엎을 필요는 없다. `APP_ENV=local`은 `config.yml`, dev/stg/prd는 각 프로파일 파일을 선택한다. 명시적인 값이 있는 YAML이 환경변수보다 우선한다.

서비스 실행 기본값에 특정 고객 모델 주소·모델명·DB 비밀번호를 넣지 않는다. Skill/Tool 예시의 데이터 경로는 업무 자산이며 서비스 실행 코드에서 해당 예시에 맞춘 분기를 추가하지 않는다. 실제 LLM·Executor·DB 연결 대상은 선택 YAML에 명시한다.

`workflow/skills`, `workflow/tools`, `workflow/workflows`의 작업 영역은 유지한다. Agent 역할 변경은 `agent_builders/<role>`, 그래프 제어 변경은 `planning`/`execution`, 실행·저장 정책 변경은 `application/runs`, HTTP 계약 변경은 `contracts/resources`와 `api_service/http`를 함께 검토한다.

이번 구조 변경은 패키지·import·조립 경계를 바꾼다. DB 테이블이나 데이터가 불필요하다고 추정해 삭제하는 migration은 추가하지 않는다. 과거 migration과 기준 성능 결과는 이력이다. 기존 실행기와 함께 신규 코드로 자동 혼재 배포하는 호환 패키지는 추가하지 않았다.

## 삭제 완료 범위

102 후속 정리에서 구형 Workflow1.3 컴파일·노트북 생성·데이터 mock·중복 schemas/tools/catalog·이전 자산 경로 alias·미등록 tmp 자산·미연결 플랫폼 routers와 전용 테스트를 삭제했다. 현재 자산 카탈로그와 실행 컴파일러는 각각 planning/catalog.py, execution/compiler.py 하나다. 등록 Skill/Tool 자산·공개 Workflow2.0·명시적 LLM mock은 유지한다. DB migration이나 기존 서비스 데이터 삭제는 없다.

## 서비스 설치와 Tool 검증 의존성

분석/학습 라이브러리 7개(pandas, pyarrow, scikit-learn, xgboost, scipy, matplotlib, seaborn)는 서버 필수 의존성에서 `tool-validation` 개발 그룹으로 옮겼다. `uv sync --locked --no-dev`로 설치하는 서비스는 이 그룹을 요구하지 않는다. 로컬 회귀/Tool 실행 검증을 위한 기본 dev 그룹에는 포함한다. 실제 Tool의 라이브러리는 Executor/Jupyter 커널에서 제공한다. HNSW 임베딩 처리에 쓰는 numpy는 서버 의존성에 남긴다. 버전 변경 없이 uv.lock을 함께 갱신했다.

Executor API 경로는 infrastructure/executor/routes.py에서 고정 규격으로 관리한다. settings/YAML은 EXECUTOR_BASE_URL과 연결·실행 옵션을 제공하며 개별 API PATH 설정은 제거했다.
