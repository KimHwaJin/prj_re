# 현재 서비스 구조와 의존성 경계

054 기준. 소스 책임을 분리하며 단일 Deployment·단일 컨테이너 Pod 전제를 유지한다.
별도 Agent HTTP 서버나 Agent별 Worker·풀을 추가하지 않는다.

```text
app.py                          기존 루트 실행 진입점
src/
  service_bootstrap.py          FastAPI 연결·Worker 시작·자원 종료 조립
  service_settings.py           config > env > 기본값 설정 snapshot
  agent_config.py / config.py / event_worker_settings.py
  api_service/
    api/                        HTTP 라우터·권한·접수
    schemas/ models/ repositories/
    services/                   CRUD·Run 상태·소유권·DB 어댑터
      agent_graph_service.py    API 실행 → 실제 분석 graph 조립 어댑터
      workflow_persistence.py   WorkflowStore의 PostgreSQL 구현
    agent_worker/               Executor 이벤트 → graph 재개 어댑터
    worker/                     Redis 수신·Inbox/Outbox·dispatch
    core/                       DB·인증·API 상태/복구 관리
    test/                       API·DB·실행기 통합 테스트
  agent_service/
    factory.py context.py middleware/
    runtime/
      model_factory.py          분석 역할과 분리한 모델 생성
      blocking.py               동기 작업의 취소/종료 수명 보호
      executor_boundary.py      LangGraph Executor 대기·receipt 노드
      langgraph/checkpointer.py 체크포인트 풀 수명
    agents/analysis/
      planning/graph.py         PlanningState·새 요청·후보·HITL·재작성
      planning/runtime.py       모델 pin·역할 캐시·Store/context 주입
      planning/catalog.py       Skill/Tool 탐색·함수 추출
      execution/                제출·관찰·판단·수정·보고서
      agent_builders/<role>/agent.py + prompt.md
      workflow/                 기존 Workflow 해석·컴파일 코드
        skills/ tools/ workflows/
      schemas/ tools/           기존 1.3 Workflow 지원
      tests/                    현재 실행/보존 자산 회귀
  service_contracts/
    events.py                   Executor 이벤트 envelope·dispatch 계약
    executor.py                 Executor HTTP 요청·접수 응답 스키마
    executor_manifest.py        공유 PV 결과 manifest 스키마
    executor_boundary.py        checkpoint 필드·ExecutionBindings Protocol
    executor_transport.py       빌려 쓰는 비동기 HTTP transport Protocol
    workflow.py                 WorkflowStore Protocol·순수 snapshot 함수
    workflow_definition.py      API·Agent 공용 Workflow JSON 규격
    execution.py                공통 복구 필요 예외
  integrations/executor/
    client.py                   HTTP·실패 분류·제출 부작용 추적
    manifest.py                 공유 PV manifest 검증·읽기
  service_runtime/
    cleanup.py                  취소 중에도 소유 자원 정리
    diagnostics.py              공유 실행 계측 context
    model_selection.py          모델 catalog·고정 참조·재개 검증
  devtools/                     같은 현재 builder의 offline mock·전체 그래프 시각화
  routers/                      플랫폼 제공 라우터 영역(별도 통합 검증 필요)
```

## 의존성 규칙

- Agent 운영 코드는 `app` 또는 `api_service`를 import하지 않는다. 저장소는
  `WorkflowStore`, 실행 연결은 `ExecutionBindings`, HTTP 클라이언트는
  `ExecutorTransport` 계약으로 전달받는다. 실제 Runtime에는 빌려 쓰는 ExecutorClient·bindings·Store를 주입한다.
- Agent는 공용 `integrations/executor` helper로 요청·결과를 처리할 수 있다.
  이 계층은 API·Agent 구현을 import하지 않는다. 클라이언트를 노드 안에서 생성하지 않는다.
- `service_contracts`, `service_runtime`, `integrations`는 API나 Agent 구현을
  import하지 않는다. 설정 snapshot과 외부 라이브러리를 사용할 수 있다.
- API 업무 서비스·라우터는 Agent 내부 graph/state/schema에 의존하지 않는다.
  실제 graph 구성은 세 어댑터에만 남긴다: `services/agent_graph_service.py`,
  `agent_worker/graph_provider.py`, `agent_worker/worker_main.py`.
  이 조립 경계까지 제거하려고 아직 필요 없는 다중 Agent registry를 추가하지 않는다.
- `WorkflowStore`와 기존 PostgreSQL 구현은 1.3 Workflow 관리 지원으로 남아 있다.
  현재 PlanningRuntime은 이 저장소를 받아 후보를 저장하는 이전 graph 경로를 사용하지 않는다.
  추천 풀·CRUD 이행은 별도 후순위이며 Agent의 직접 DB 조회를 추가하지 않는다.
- `cleanup.py`는 자원 종료만 담당한다. API의 `execution_lifecycle.py`는 DB 복구
  표시와 실행 건강 상태를 관리한다. 둘을 중복 구현하지 않는다.

## Agent 개발자가 수정할 곳

1. 새 요청·HITL·상태는 `agents/analysis/planning/graph.py`, 실행·조건 판단은 `execution/nodes.py`, 수정은 `execution/repair_nodes.py`.
2. 역할별 모델·도구·미들웨어 선언과 독립 프롬프트는 `agent_builders/<role>/`.
3. 기존 Workflow 개발은 **`agents/analysis/workflow/`**를 계속 사용한다.
   skills·tools·workflows와 생성기는 이동하거나 복제하지 않았다.
4. 공개 Workflow JSON 또는 Executor 규격 변경은 `service_contracts`에서
   API·Agent·연동 소비자 영향을 함께 검토한다. 저장 구현 변경은 API 어댑터에서 한다.
5. 계층 검증은 `src/api_service/test/test_package_boundaries.py`, 업무 회귀는
   `src/agent_service/agents/analysis/tests`에서 실행한다.

## 실행·이전 경로

- 기존 `python app.py`, `uvicorn main:app --app-dir src`, 배포 bootstrap 유지.
- 독립 이벤트 Worker 모듈은 `python -m api_service.agent_worker.worker_main`.
- Alembic ORM import·compose·배포 YAML·langgraph.json·wheel 설정을 새 경로로 갱신했다.
- `src/app`와 이동한 기존 파일의 호환 shim은 남기지 않는다. 직접 import하거나
  모듈 실행 명령을 별도로 관리하는 소비자는 새 경로로 변경해야 한다.
- 과거 개선 보고서·이동 목록·고정된 과거 커밋 비교 도구의 경로는 당시 근거로 보존한다.
- 그래프 node 이름·state 필드, 공개 API, DB migration revision은 변경하지 않는다.
  현재 계획 그래프의 이전 PostgreSQL HITL checkpoint 재개를 별도로 검증한다.
  조건부 다이어그램 연결 표시는 보완하지만 노드·선택 로직은 유지한다.

## 남은 범위

실행 제어 서비스와 Worker는 현재 `api_service`의 내부 계층으로 유지한다.
project_memory는 공식 Store와 역할별 middleware로 구현했다.
별도 `execution_service` 패키지, 다중 업무 Agent registry,
관리자 복구 API, Message/Workflow 관리 정리는 이번에 구현하지 않는다.

HTTP는 native async이며 파일/PV와 Workflow DB 저장은 기존 `run_sync` 수명을 유지한다.
제공 Gaia 템플릿 전체 기동·폐쇄망·Kubernetes·외부 Executor/Redis 배포 검증도 별도다.
