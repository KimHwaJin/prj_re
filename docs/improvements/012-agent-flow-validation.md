# 012 — Agent 업무 흐름 회귀 정상화·동기 I/O 취소 수명

- 날짜: 2026-09-28
- 브랜치: feature/refactor-agent-flow-validation
- 출발: c13a541 (007~011을 feature/refactor-base에 fast-forward 병합한 뒤 분기)
- 상태: 구현·오프라인 검증 완료. 이번 012의 베이스 병합·원격 push·배포 미수행.

## 문제와 근거

011 기준 전체 오프라인 결과는 225 passed / 19 failed / 39 skipped였고, 존재하지 않는 Tool을 import하는 테스트 파일 2개는 수집에서 제외했다. 이번에는 같은 실패를 먼저 재현했다.

| 기존 문제 | 원인과 처리 |
|---|---|
| Workflow 생성 관련 8개 실패 | ee66fac에서 신규 WorkflowPlan에 ready·빈 입력 필드를 요구하도록 변경했으나 테스트의 PlanWorkflowAgent는 needs_input을 반환했다. 생성 fixture와 승인/거절/재선택 기대값을 현재 규격으로 수정했다. 저장된 추천 Workflow의 needs_input 처리·답변·승인·파일 기록 테스트는 유지했다. |
| 카탈로그 관련 8개 실패 | 과거 삭제된 data_cleaning_pipeline, eda_analysis, predictive_modeling 또는 미등록 failure_analysis에 의존했다. 운영 카탈로그는 변경하지 않고 현재 Skill로 검증하거나 테스트 전용 조건부 Skill을 주입했다. 숫자 기본값 정규화도 합성 registry 입력으로 독립 검증한다. |
| 조건 선행 관계 관련 2개 실패 | 삭제된 Skill의 규칙이 없어 검증 대상 자체가 없었다. 기존 Tool 소스에 테스트 전용 조건 관계를 부여해 누락·순서 역전·정상 순서를 검증한다. |
| Executor 경로 관련 1개 실패 | 공유 루트 기준 상대 PATH를 절대 경로로 기대했다. 실제 공유 루트 아래 파일 내용과 SHA-256을 검증하도록 변경했다. |
| 수집 오류 2개 | ee66fac에서 select_features.py·split_dataset.py 구현을 명시적으로 삭제했다. 남아 있던 구현 전용 테스트 파일도 제거하고 배포 registry/패키지에서 해당 Tool을 노출하지 않는 계약 검증으로 대체했다. 삭제한 기능을 복원하거나 수집 제외/xfail로 덮지 않았다. |

스키마 실패 뒤에 가려졌던 async 테스트 내부의 asyncio.run 2곳도 await로 수정했다. 이름만 재시도 테스트였으나 실질적으로 재시도하지 않던 fixture는 첫 Plan에 잘못된 argument를 주고 두 번째 요청의 validation_feedback·previous_workflow를 검증하도록 고쳤다.

## 실제 코드에서 재현한 취소 문제

실제 분석 graph의 Executor 제출 노드와 catalog 저장 노드에 정지 가능한 동기 I/O를 주입했다. 취소하면 둘 다 I/O 종료 전에 graph.ainvoke가 취소 완료로 반환했다(수정 전 회귀 2 failed). LangGraph가 def 노드를 스레드로 넘기는 것과 그 스레드가 끝날 때까지 Run이 기다리는 것은 별개였다.

`graph.py`의 `add_io_node`가 기존 `runtime.blocking.run_sync`를 통해 동기 I/O 노드 12개를 등록한다. 취소 후에도 진행 중인 호출이 종료될 때까지 await 상태를 유지하고, 완료 후 CancelledError를 전파한다. 반복 취소에도 동일하게 동작한다. copy_context를 통해 HITL의 재개 문맥도 보존한다. 순수 상태 변환 노드와 영속 Executor interrupt는 기존 등록을 유지한다. 노드 이름·edge·외부 API·DB 스키마는 변경하지 않았다.

이 변경은 동기 HTTP·DB·파일 구현을 native async로 전환한 것이 아니다. 외부에 이미 제출된 작업을 취소하거나 DB 쓰기를 롤백하지도 않는다. 진행 중인 호출의 소유권을 놓지 않고, 그래프가 종료된 뒤에도 그 호출이 뒤늦게 수행되는 상황을 막는 단계다. HTTP timeout은 기존 설정을 유지한다. DB/PV가 응답하지 않는 경우 취소 완료도 지연될 수 있으며 제한 시간·native async port·실행 동시성 예산은 후속이다. 강제 프로세스 종료 이후 복구/중복 실행을 보장하는 변경으로 해석하지 않는다.

## I/O 경로 점검 결과

| 실행 경로 | 현재 처리 | 남은 일 |
|---|---|---|
| LLM 역할 7개 | create_agent → ainvoke, 006·011에서 native async HTTP 검증 | 모델 선택·공통 요청 예산 |
| Workflow 생성의 카탈로그 읽기·컴파일 | async 노드 안에서 기존 run_sync | 카탈로그 수명/계산 비용 계측 |
| 후보 catalog 저장·후보 선택 파일 저장·승인 Workflow 저장 | 이번 add_io_node로 호출 수명 관리 | DB async 저장 port, 파일 원자적 쓰기/ArtifactStore |
| Notebook 코드 생성·PATH 소스 staging·Executor 제출/추가 제출/종료/취소 | 이번 add_io_node로 호출 수명 관리 | HTTP 연결 재사용·native async, PV 원자적 staging |
| 실행 결과 API/manifest 조회·DB 실행 결과 기록 | 이번 add_io_node로 호출 수명 관리 | 응답 크기/시간 제한, DB 풀·트랜잭션 관리 |
| 조건 판단·보고서 작성 | async LLM + 기존 run_sync로 동기 저장/보고서 제출 | 공통 저장/HTTP port로 통합 |
| Executor 외부 작업 대기 | interrupt checkpoint 후 현재 ainvoke 반환 | 장기 보존·운영 복구·버전 호환 검증 |

이번 점검에서 살펴본 분석 graph 경로에 동기 HTTP/파일 호출을 async 노드 안에서 직접 수행하는 새 사례는 확인하지 못했다. 일반 def 노드는 이미 스레드로 실행되므로 모두 이벤트 루프를 막는다고 결론 내리지 않는다. 새로 입증한 문제는 취소 때 스레드 작업이 Run보다 오래 살아남는 수명 불일치다. 전체 서비스의 지연 원인을 이 문제 하나로 확정하지 않는다.

## 업무 흐름·검증

- 생성 → 후보 선택 → 승인, 거절 후 수정, 데이터 재선택, 추천 없음/비활성, 추천 Workflow의 추가 입력/승인 경로 검증.
- 실제 분석 graph로 승인 → Executor 제출(Mock) → EXECUTOR_EVENT interrupt까지 실행. 대기 중 graph 객체를 재생성하고 동일 InMemorySaver에서 이벤트 adapter로 재개.
- SUCCEEDED는 결과 읽기·보고서 생성·보고서 API(Mock), FAILED는 보고서 생략. receipt를 확인하고 같은 command/event를 재전달해 제출·결과 조회·보고서가 반복되지 않음 검증.
- 제출/카탈로그 저장 중 취소 후 I/O가 끝날 때까지 graph가 반환되지 않으며 다음 binding 등록이 수행되지 않음. 실제 HTTP·DB 서버 대신 종료를 통제할 수 있는 주입 함수를 사용했다.
- 집중 회귀: 60 passed. 조건부 실행/컴파일/선행 관계: 22 passed (추가 subtest 2개).
- **전체: 251 passed / 0 failed / 39 skipped**, 수집 제외 옵션 없음. 경고 42개는 011에 기록한 saver 없는 내부 Agent의 durability 경고로 유지된다.
- 운영 Skill/Tool 자산과 역할별 프롬프트는 변경하지 않았다. 테스트용 조건부 카탈로그는 각 테스트가 끝나면 복원되고 wheel에는 tests가 포함되지 않는다.
- wheel 패키지 검증: 7개 역할·독립 프롬프트·checkpointer=False, OpenAPI 33개, Mock 승인/제출 6 steps, 이전 패키지와 tests 미포함.

[구조화 검증 결과](../reports/agent-flow-validation-2026-09-28.json).

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest \
  src/app/test src/agent_service/agents/analysis/tests -q
```

실제 PostgreSQL 조건부 테스트 39개는 실행하지 않았다. InMemorySaver 재조립 검증은 실제 Pod 재시작·PostgreSQL 복구 검증을 대체하지 않는다. 실제 Redis/LLM/Executor, 컨테이너·원본 checkout은 변경하지 않았다. 운영 부하·성능 개선 수치는 측정하지 않았다.

## 다음 작업

업무 Agent 등록/선택과 재개 대상 고정 → main_model_name 선택/재개 고정 → project_memory 저장·미들웨어 연계를 이어간다. 공통 실행 동시성·lease/fencing·복구·CRUD 정책·Gaia 통합·native async 저장/HTTP는 남은 전체 리팩터링 범위다.
