# 서비스 구조와 의존성 경계

101 API 정리 기준이다. 단일 Deployment·단일 컨테이너 Pod에서 `app.py`가 API·Agent Worker·Executor 이벤트 수신을 조립한다. API 세부 파일 이동은 [API 구조·삭제 추적](../api-service-layout.md), 실행 계약은 [공통 Worker](../agent-command-worker.md)를 따른다.

```text
app.py
src/
  service_bootstrap.py          FastAPI·background task·자원 수명 조립
  service_settings.py           선택 YAML > env > 기본값 snapshot
  config.py                     API Settings
  agent_config.py               모델·Agent·checkpoint·Executor 실행 설정
  event_worker_settings.py       원본 Streams 수신·routing 설정
  api_service/
    api/                        HTTP 입력·dependency·pagination·problem response
    infrastructure/             DB·Store·Executor binding 자원
    models/ schemas/            현재 DB·REST 모델
    repositories/ resources/    CRUD·조회·SSO 최초 등록·소유권·삭제 정책
    runs/                       접수·실행·취소·조회·로그·SSE·Task
      runtime.py                실제 Agent graph 조립 어댑터
      commands/                 단일 DB 원장 admission·claim·outcome·wakeup
      protocols/                시작·사용자 승인·Executor receipt
      persistence/              결과·계획·메시지·이벤트 저장
    workers/                    공통 Agent Worker·재조정
      executor_events/          Redis 수신·Inbox·순서·binding
    workflows/ search/          Workflow CRUD·JSON·pgvector 추천
    web/                        /demo·HTML
  agent_service/
    factory.py context.py middleware/
    runtime/                    모델 생성·blocking 수명·Executor 경계·checkpointer
    agents/analysis/
      planning/                 그래프·역할 조립·Skill/Tool catalog
      execution/                제출·관찰·판단·수정·리포트
      agent_builders/<role>/    역할별 Agent 선언·프롬프트
      workflow/                 기존 Workflow 작업 패키지
        skills/ tools/ workflows/
      schemas/ tools/ tests/
  service_contracts/            공용 Workflow·Executor·memory·event 계약
  integrations/executor/        HTTP·manifest·artifact 어댑터
  service_runtime/              공용 정리·진단·모델 선택·Phoenix
  service_auth/                 SSO adapter·Redis 로그인 세션·CSRF
  devtools/                     현재 builder의 offline mock·그래프 시각화
  routers/                      플랫폼 제공 라우터 영역

tests/api_service/              API·PG·Streams·schema 이행 회귀
crud_migrations/                공통 API/명령/Store 스키마 이력
migrations/                     이벤트 Inbox/binding 이력·구 객체 폐기
```

## 의존성 규칙

- Agent 구현은 `app`·`api_service`를 import하지 않는다. 실제 Runtime에는 ExecutorClient·ExecutionBindings·LangGraph Store·Workflow 검색 adapter를 주입한다.
- `service_contracts`, `service_runtime`, `integrations`, `service_auth`는 API·Agent 구현을 import하지 않는다. API 조립부가 이를 사용한다.
- API에서 실제 Agent 구현을 import하는 곳은 `api_service/runs/runtime.py` 하나다. HTTP 라우터와 이벤트 수신부는 Agent 내부 graph/state에 의존하지 않는다.
- 공통 Agent Worker가 사용자와 Executor 입력을 같은 원장·한도로 실행한다. 이벤트 수신부는 원본 이벤트 저장·순서 보장·명령 접수까지만 담당한다.
- `service_runtime/cleanup.py`는 자원 종료, `api_service/runs/lifecycle.py`는 DB 실행 상태와 복구 표시를 담당한다.
- 구 WorkflowStore·별도 Workflow DB·EW shadow 원장·직접 LLM 구현은 삭제했다. 현재 추천은 `workflows`·`workflow_embeddings`, 프로젝트 메모리는 공식 Store를 사용한다.

## 개발자가 수정할 곳

1. HTTP 요청/응답: `api/v1/routes`·`schemas`; DB 모델: `models`; CRUD 정책: `resources`.
2. Run 접수·조회·취소: `runs`; graph 결과 저장: `runs/persistence`; 실행 순서·점유: `runs/commands`·`runs/ownership.py`.
3. Agent 새 요청·HITL: `agents/analysis/planning`; 실행·판단·수정: `execution`; 역할 선언·프롬프트: `agent_builders/<role>`.
4. 기존 Workflow 자산은 `agents/analysis/workflow`에서 계속 관리한다. 예시 Skill/Tool에 맞춘 하드코딩을 추가하지 않는다.
5. 공용 JSON/Executor/메모리 계약 변경은 `service_contracts`에서 API·Agent·프론트 영향을 함께 검토한다.
6. API 계층 검증은 `tests/api_service/test_package_boundaries.py`, Agent 회귀는 `src/agent_service/agents/analysis/tests`다.

## 실행과 적용

배포 진입점은 `python app.py`다. Alembic은 [migration launcher](../database_migrations.md)로 먼저 적용한다. 기존 writer를 종료한 뒤 새 코드로 전환하며 구·신 스키마 롤링 혼재는 지원하지 않는다. `/demo`는 같은 프로세스의 HTML 경로이고 독립 프론트 서버는 필요 없다.

101은 API 정리다. Agent의 노드명·state·checkpoint·LangChain 구성과 플랫폼 제공 core는 변경하지 않는다. 다중 Agent registry, 폐쇄망 Gaia 전체 기동, 실제 Kubernetes/외부 인프라 배포는 별도 범위다. 과거 revision·측정 보고서·고정 커밋 비교 도구는 기존 DB 이행과 당시 결과 재현을 위한 이력이다.
