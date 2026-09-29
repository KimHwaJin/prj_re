# 024 — Executor HTTP 비동기 호출·연결 수명

- 날짜: 2026-09-29
- 브랜치: `feature/executor-async-http`
- 기준: `009e996` (023까지 feature/refactor-base 병합 완료)
- 상태: 구현·로컬 HTTP/격리 PostgreSQL/전체 회귀/패키지 검증 완료. 배포 미수행.
- 구현·검증 커밋: `cc490d5`. 현재 개선 브랜치는 베이스 미병합. 원격 push·배포 미수행.

## 문제와 범위

기존 `app/services/executor_client.py`의 urllib 요청은 동기식이었다. LangGraph의
스레드 경계 덕분에 이벤트 루프를 직접 점유하지는 않지만, HTTP 응답을 기다리는 동안
스레드를 점유하고 취소도 그 스레드의 실제 반환을 기다려야 했다. 요청마다 독립적인
urlopen/TLS 설정 경로를 거쳤으며 애플리케이션 소유 연결 풀의 재사용/상한이 없었다.

이번에는 HTTP를 native async로 전환하고 명시적으로 수명을 관리한다. 파일/PV와
기존 WorkflowStore의 저장소 전환, 관리자 복구 API, 제출 outbox, Message/Workflow
관리 API, 프로젝트 메모리는 범위에서 제외했다.

## 실제 변경

1. `ExecutorClient`가 httpx.AsyncClient를 소유한다. 시작/추가 Operation/finalize/
   실패 후 cancel/artifact 업로드와 execution/result/notebook GET 전체를 await한다.
   요청 body의 기존 idempotency_key를 그대로 전달하고 HTTP 자동 재시도는 하지 않는다.
2. API AgentGraphRuntime과 Executor 이벤트 Worker 각각 클라이언트 한 개를 생성해
   그래프에 주입한다. API는 기존 graph borrow drain 이후 닫으며 초기화 실패에서도
   정리한다. 모델별·Run별·노드별 HTTP 풀을 만들지 않는다.
3. Executor 관련 동기 노드를 async def로 바꾸고 builder.add_node로 등록했다.
   파일 staging/manifest 읽기/WorkflowStore 저장은 run_sync로 분리한다. 동기 테스트
   어댑터는 call_io의 기존 소유권 보호 스레드 경계로 지원한다.
4. 중앙 설정에 연결 수/연결 대기/풀 대기/응답 byte 상한 4개를 추가했다.
   기존 EXECUTOR_TIMEOUT_SECONDS는 호출 전체 기한 및 read/write 기한으로 사용한다.
   기본값과 우선순위는 [사용 가이드](../executor-http-runtime.md)에 기록했다.
5. 기존 TLS 검증 설정과 기본 SSLContext CA 정책을 유지한다. 클라이언트 생성 시
   한 번 적용하며 redirect/ambient HTTP proxy 자동 사용은 하지 않는다.
6. 전달 전 실패·명확한 거절과 접수 여부 불확실성을 구분한다. POST 후 응답 유실,
   timeout/취소/408/409/5xx/redirect/잘못된 receipt는 ExecutorOutcomeUnknown으로
   기존 ExecutionNeedsRecovery 경로를 타며 자동 재제출하지 않는다.
7. invocation별 SubmissionEffects를 자식 task에 전달한다. POST 성공 이후 내부 오류나
   취소, 그래프 완료와 취소 watcher의 동시 완료도 보호한다. 병렬 요청 중 거절된 한
   요청이 다른 접수 가능성 기록을 지우지 않도록 시도별로 관리한다.
8. RunService 정리에서 이미 끝난 graph task의 복구 필요 예외도 관찰한다. 취소 watcher가
   먼저 선택됐다는 이유로 그 예외를 삼켜 세션을 취소 완료로 해제하지 않는다.
9. 기존 수동 실행 도구의 PostgreSQL/메모리 경로도 HTTP 수명을 관리하도록 연결했다.
   새 CLI/관리 API는 추가하지 않았다. 참조가 없고 제거된 polling 설정을 사용하던
   `app/services/execution_waiter.py`는 삭제했다. 실제 이벤트 대기 경로는 유지했다.

DB 스키마, 외부 Executor request 규격, key 생성 규칙, 그래프 노드 이름/edge 및
장기 Executor interrupt 흐름은 변경하지 않았다.

## 테스트와 관측

실제 외부 Executor·LLM·Redis를 호출하지 않았다. 로컬 HTTP/1.1 서버와 새 임시
PostgreSQL 17의 identity_test, 실제 API/Worker/세션 소유권 구현으로 검증했다.

- HTTP/설정/실제 graph 테스트 **30건**: native async 연결, 설정 오류, 반환형/receipt,
  응답 제한, 명확한 거절, 접수 불명, 동시 요청 및 취소 경합.
- 실제 PostgreSQL/API 통합 **7건**: POST 응답 유실, cancel API, 실행 중 종료,
  응답 후 내부 저장 실패 모두 recovery_required 및 소유권 유지. 신규 접수 409,
  자동 재시도 없음, HTTP 서버 접수 1회. 알려진 422는 복구 표시 없이 오류 종료.
  Executor 이벤트에서 보고서 응답 유실도 세션 소유권을 격리한다.
- 위 HTTP와 기존 runtime 수명 집중 검증: **44 passed**, 1.67초.
- 기존 파일/PATH/INLINE/manifest/보고서 이벤트 흐름 검증: **17 passed**.
- DB/기존 취소/결과 읽기 집중 검증: **25 passed**.
- 최종 전체 회귀: **560 passed + 2 subtests**, 53 warnings, **196.09초**.
  TLS 신뢰 설정 보존과 잘못된 URL의 전달 전 오류 분류 보완까지 포함한 결과다.
- wheel 격리 검증: 소스 checkout import 없이 API 34경로, 역할 Agent 7개,
  mock graph 6단계 및 프롬프트/Workflow 자산 포함 확인. 최종 wheel 재검증 통과.
- 변경 Python 파일 23개의 compile 검사 및 git diff --check 통과.
- 검증 후 이번 작업용 임시 PostgreSQL 컨테이너와 볼륨을 삭제했다. 기존 서비스/DB는 변경하지 않았다.
- [기계 판독 검증 기록](../reports/executor-async-http-validation-2026-09-29.json)

| 관측 시나리오 | 실제 결과 |
|---|---|
| POST 5종 + GET 3종 순차 실행 | HTTP 요청 8회, TCP 연결 1개 재사용 |
| 최대 연결 2개에서 GET 6회 | 서버 동시 처리 peak 2, 연결 2개, 6회 모두 완료 |
| 연결 1개 점유 중 다음 요청의 pool timeout | 다음 POST가 서버에 도달하지 않음 |
| 원격이 접수한 뒤 응답 연결 종료 | HTTP 자동 재전송 0회, 복구 필요 예외 |
| 정확한 원본 key/body를 명시적으로 재전송하는 mock 계약 검증 | 접수 요청 2회, 생성된 원격 작업 1개 |
| 실제 분석 graph 승인 → HTTP 제출 | 접수 1회 후 EXECUTOR_EVENT interrupt 반환, 작업 완료를 기다리지 않음 |
| drain 중 실행 중인 HTTP | 요청 종료까지 client 유지, 이후 close, 신규 borrow 거절 |

이 수치는 통제된 계약 테스트의 관측값이다. 실제 Executor 처리량·지연 개선 배수나
최적 pool 크기로 해석하지 않는다. 서로 다른 집중 테스트 수는 중복이 있어 합산하지 않는다.

## 운영 영향과 남은 제한

- 접수 불확실 시 기존 fail-closed 정책대로 해당 프로세스의 신규 claim도 중단된다.
  DB의 Run/Task 또는 이벤트 세션 실행 보호가 남는다. 재시작만으로 잠금을 해제하지 않는다.
- 자동 복구/원격 접수 조회/제출 outbox는 구현하지 않았다. 정확한 원본 key/body와 원격
  상태 확인 없이 graph를 처음부터 다시 실행하면 안 된다. PATH staging의 날짜 경로 등은
  재실행 시 달라질 수 있다. 이번 변경은 그런 불확실한 자동 재실행을 막는 단계다.
- 런타임별 HTTP 상한은 프로세스/Pod/replica 전체의 전역 상한이 아니다. API와 이벤트
  런타임이 함께 있으면 그래프용 풀이 두 개다. 별도로 이벤트 ingress의 기존 비동기
  reconciliation 조회 클라이언트도 있으며 해당 EW 설정은 유지했다. DB·LLM 풀도 별개다.
- 실제 사내 Executor/TLS/프록시 환경, Redis 전달 경로와 Kubernetes 배포는 미검증이다.
- 전체 src compileall 점검에서는 기준 커밋에도 존재하는 제공 템플릿
  `src/routers/chat/router.py:3`의 `import typing import List, Optional` 문법 오류를 확인했다.
  이번 변경 파일은 아니며 수정하지 않았다. app/agent_service 대상 회귀와 wheel 검증의
  성공을 실제 Gaia 템플릿 전체 통합 검증 완료로 해석하지 않는다.

[HTTP 개발·운영 가이드](../executor-http-runtime.md)
