# 025 — API·Agent 의존성 분리와 패키지 구조 마무리

- 날짜: 2026-09-29
- 브랜치: `feature/api-agent-boundaries`
- 기준: `26a9295` (024 구현·기록까지 베이스 병합 완료)
- 상태: 구현·격리 PostgreSQL/전체 회귀/체크포인트 재개/패키지 검증 완료. 배포 미수행.
- 구현·검증 커밋: `8f45ebb`. 현재 개선 브랜치는 베이스 미병합. 원격 push·배포 미수행.

## 문제

분석 Agent 코드는 agent_service로 집약했지만 Executor HTTP, Workflow 저장,
Executor 경계 상태/노드, 실행 계측을 app에서 가져왔다. graph import만으로도
API Worker/DB 구현이 따라 로딩되는 경로가 있었다. 반대 방향으로도 API 업무
서비스가 Agent의 모델 선택 및 Workflow JSON 규격에 의존했다.
루트 app.py와 src/app의 이름 충돌을 피하는 sys.path 우회도 남아 있었다.

## 실제 변경

1. 기존 src/app을 **src/api_service**로 이동하고 내부 import/테스트/mock patch 경로를
   갱신했다. 이전 패키지나 forwarding shim은 남기지 않는다. 실제 source 책임을
   분리하는 작업이며 별도 Pod/서버/Worker를 추가하지 않는다.
2. service_contracts에 Executor 요청/응답·event·manifest·경계 state/Bindings,
   WorkflowStore/순수 snapshot·Workflow JSON 스키마·복구 필요 예외를 분리했다.
   ExecutorTransport Protocol은 런타임 소유 클라이언트를 빌려 사용하는 규격이다.
3. integrations/executor에 기존 HTTP client와 manifest 읽기를 이동했다.
   외부 연동 계층은 API·Agent 구현을 import하지 않는다. 기존 요청/body/key,
   실패 분류·제출 부작용 추적·연결 수명은 그대로다. Agent는 이 공용 helper를
   사용할 수 있으며 HTTP 클라이언트의 생성·종료는 서비스 런타임이 담당한다.
4. WorkflowStore Protocol과 Null 구현은 공용이며 PostgreSQL 구현은
   api_service/services/workflow_persistence.py에서 생성·주입한다. 동기 저장은
   기존 run_sync 경계를 유지한다. 저장소와 순수 snapshot 기능이 Agent에서
   PostgreSQL 드라이버/API 내부 코드의 import를 요구하지 않는다.
5. LangGraph Executor 대기/receipt 노드는 agent_service/runtime/executor_boundary.py로
   옮겼다. event 계약에 LangGraph를 끌어들이지 않는다.
6. service_runtime에 protected_cleanup·실행 계측·모델 선택을 이동했다.
   API의 execution_lifecycle은 기존 DB 복구 기록/health를 유지하며 공통 예외를 쓴다.
7. API에서 실제 Agent 구현을 import하는 곳은 세 조립 어댑터로 제한한다.
   services/agent_graph_service.py, agent_worker/graph_provider.py,
   agent_worker/worker_main.py. 다중 Agent registry를 새로 구현하지 않았다.
8. bootstrap·관리자 초기화·Alembic env·compose·배포 YAML·langgraph.json·개발 도구·
   wheel package/data 설정을 갱신했다. root app.py와 main:app 진입점은 유지한다.
   이름 충돌용 bootstrap sys.path 조작은 제거했다.
9. [현재 구조와 개발 책임](../architecture/service-layout.md), 개발/설정/HTTP 가이드를
   갱신했다. 과거 보고서·고정 커밋 비교 도구는 당시 증거를 가리키므로 보존했다.

역할별 agent_builders 및 prompt.md, analysis/workflow의 skills·tools·workflows와
생성기는 유지한다. 공개 API·업무 흐름·graph node/edge/state 필드·DB migration
revision은 변경하지 않는다. Message/Workflow 기능 정리와 project_memory는 범위 밖이다.

## 검증

- 공개 OpenAPI **34경로 전체 문서가 기준 커밋과 동일**(Python dict 비교).
- 새 계층 테스트 **6건**: Agent/공통 계약/런타임/연동에서 API 역참조 금지,
  API의 Agent import를 조립 경계에 제한, API import를 차단한 별도 Python 프로세스에서
  실제 mock graph를 HITL 4회 포함 제출 생략 완료까지 실행.
- 기준 26a9295 소스를 별도 임시 디렉터리에 내보내 실제 PostgreSQL checkpoint에
  select_workflow_candidate 승인 대기를 저장했다. 새 코드·새 프로세스에서 재개해
  mock execution step 6개 생성 및 정상 종료를 확인했다. graph node/edge 목록도 동일했다.
  scripts/diagnostics/validate_package_transition.py로 재현할 수 있다.
- 1차 전체 회귀: 559 passed, 1 failed, 2 subtests. 관리자 초기화 도구의 Path import를
  불필요한 경로 우회와 함께 제거한 실수를 수정했다.
- 최종 전체 회귀: **566 passed + 2 subtests**, 53 warnings, **201.98초**.
  신규 경계 테스트 6건을 포함하며 skip/failure 0건이다.
- wheel 격리 실행: 기존 app 패키지 제외, api_service/agent_service/service_contracts/
  service_runtime/integrations 포함, 소스 checkout import 없음, 역할 7개·프롬프트·
  Workflow 자산·API 34경로·mock graph 6단계 검증 통과.

- 변경 Python 파일 **190개 compile 검사**, git diff --check 통과.
- 테스트 전용 PostgreSQL 컨테이너/볼륨 제거 완료. 기존 서비스·DB는 변경하지 않았다.
- [검증 결과 JSON](../reports/api-agent-boundaries-validation-2026-09-29.json)

전체 회귀 명령:

```sh
PYTHONPATH=src DTEST_IDENTITY_TEST_DATABASE_URL=<격리 로컬 identity_test DSN> \
  python -m pytest src/api_service/test src/agent_service/agents/analysis/tests \
  -q --disable-warnings --maxfail=2
```

## 영향과 제한

- 기존 app.* 또는 이동한 Agent schema/model_selection을 직접 import하는 외부
  도구는 새 경로로 갱신해야 한다. 이전 파일을 남기는 호환 계층은 만들지 않는다.
- 실행 제어와 이벤트 Worker는 api_service 내부에서 유지한다. 별도 execution_service
  분리/다중 Agent registry까지 완료했다는 의미가 아니다.
- 체크포인트 검증은 현재 분석 graph의 이전 HITL snapshot과 현재 회귀 범위다.
  임의의 과거 코드/사용자 정의 Python 객체 직렬화/모든 장기 실행 버전의 호환을 보장하지 않는다.
- 성능 변경을 목표로 한 작업이 아니며 처리량 향상 수치를 주장하지 않는다.
- 외부 LLM·Executor·Redis, 실제 Kubernetes·폐쇄망 Gaia 통합은 미검증이다.
  기존 제공 src/routers/chat/router.py의 문법 오류는 범위 밖으로 유지했다.
- 관리자 복구 API·접수 불확실 자동 복구·project_memory·Message/Workflow 관리는 후속이다.
