# 059 Run 책임 분리와 공통 GraphInvocation

| 항목 | 내용 |
|---|---|
| 상태 | 구현·관련 회귀·격리 PostgreSQL 검증 완료 / 베이스 미병합·미배포 |
| 시작일 / 완료일 | 2026-10-03 / 2026-10-03 |
| 브랜치 | feature/run-execution-boundaries |
| 기준 | c5abebd — 058 배포·설정 정합성 구현 |
| 관련 합의 | D-03/D-04/D-05, 다음은 D-06/D-08/D-09 |

## 기존 문제

`RunService` 한 클래스가 요청 접수·실행·취소·재시도·최종 상태 기록을 담당했다. API, Worker, projection이 클래스의 private method를 호출했고, 접수 메서드에 `_execute_existing`을 넣어 이미 접수한 작업의 실행에도 사용했다. 호출자가 접수와 실행의 서로 다른 전제를 알기 어려웠다.

사용자 입력과 Executor 결과가 다른 graph 어댑터를 통해 실행돼 프로젝트 context, 모델 검증, stream projection, 제출 추적의 변경 위치가 분산돼 있었다. 기존 실행부는 그래프 전에 commit하더라도 동일 AsyncSession 객체를 그래프 실행과 최종 기록까지 유지했다. 이는 항상 연결을 점유했다는 뜻은 아니지만, 실수로 ORM 접근·새 transaction을 실행 대기 구간에 넣기 쉬운 구조였다.

## 실제 변경

`api_service/runs`를 Run 실행의 application 패키지로 만들었다. API는 `admission.enqueue`와 `cancellation.cancel_task`, Worker는 `execution.execute_claimed`를 직접 호출한다. 접수 메서드로 실행하는 우회와 private method 의존을 제거했다.

- `execution.prepare`는 현재 Worker의 immutable claim을 확인하고, 짧은 DB 작업에서 identity·모델·project 값을 복사한다. 준비 session을 닫은 뒤에만 그래프를 호출한다.
- `execution.invoke`는 heartbeat·취소 감시·token observer 수명을 소유한다. 준비용 DB session이나 ORM 객체를 그래프에 전달하지 않는다.
- 결과·실패 반영은 별도의 새로운 DB session에서 수행한다. receipt가 있으면 graph 입력을 재전달하지 않고 서비스 상태를 복구한다.
- `GraphInvocation`이 최초 입력, 사용자 승인 resume, Executor event resume의 공통 호출·projection·submission scope를 담당한다. 각 경로의 입력 identity와 receipt 검증은 `protocols`로 분리했다.
- 중첩 graph 호출이 취소 감시의 Executor 제출 tracker를 공유하게 했다. 서로 다른 invocation은 tracker를 공유하지 않는다. 제출 결과가 불명확한 취소를 일반 재시도로 처리하지 않는다.
- `errors`의 의미별 예외는 HTTP에 의존하지 않는다. 공개 HTTP 응답 변환은 `core/problems.py` 한 곳에서 처리하며 기존 404/409/422/502/503 계약을 유지한다. 플랫폼 app에도 동일 handler를 등록한다.
- `repository`는 행 잠금·identity 조회, `requests`는 모델 선택과 멱등 요청 비교, `policy`는 기존 재시도 판정을 담당한다.

기존 `run_service.py`, `initial_request_service.py`, `user_resume_service.py`, `executor_completion.py`, `agent_worker/langgraph_adapter.py`는 호출부와 테스트를 이행한 뒤 삭제했다. 호환 facade를 남기지 않았다. 진단 스크립트는 새 소스의 경계를 사용하며, 이전 소스와 비교하는 profiler만 선택한 기준 소스의 구 인터페이스도 지원한다.

## 사용자 동작과 보존한 계약

공개 Run ID·API path/body·SSE·interaction/resume token을 바꾸지 않았다. 같은 세션의 실행 잠금과 WAITING_EXECUTOR 입력 잠금, 다른 세션의 독립 진행을 유지한다. 모델 pin, 프로젝트 prompt/kernel snapshot, Store 기반 project_memory, 입력/승인/이벤트 receipt, sync durability를 보존한다.

HITL·Executor 대기에 도달하면 현재 invocation의 실행 소유권을 반환한다. 외부 Executor가 일주일 동안 실행돼도 준비 session이나 Agent 실행 자리를 그 기간 동안 보유하지 않는다. Agent 노드·role prompt·LLM 호출 수·Executor API/Redis 계약은 이번 범위에서 변경하지 않았다.

## 검증

최종 결과는 [원본 로그·재현 방법](../reports/run-execution-2026-10-03/README.md)에 기록했다. API·Agent 회귀 595개 통과/342개 조건부 skip, 실제 PostgreSQL 관련 84개 통과, 마지막 결과 반영 경계 3개 재확인 통과다. 로컬/플랫폼 app의 Run·SSE OpenAPI와 의미별 오류 handler도 확인했다. 실제 PostgreSQL session 수명·체크포인트 receipt 복구, 모델 pin, 중복 승인, 세션 잠금, 결과 저장 재시도, decision/repair HITL을 확인한다.

구조를 나눴다는 이유만으로 처리량 향상을 주장하지 않는다. 이번 단계는 다음 공통 스케줄러의 책임 경계를 만드는 작업이다. 실행 한도 변경이나 전후 부하측정은 수행하지 않았다.

## 인수인계와 다음 단계

[파일별 책임·호출 경계·DB 수명](../run-execution-architecture.md)을 따른다. API DB UoW와 LangGraph checkpoint commit은 독립이며 receipt 기반 복구가 필요하다.

현재 사용자 Run dispatcher와 Redis 이벤트 dispatcher는 둘로 유지된다. 다음 단계는 새 요청·사용자 resume·Executor resume을 DB 내부 명령 원장에 기록하고 하나의 Agent Worker가 같은 총 실행 한도에서 배분하도록 만드는 것이다. Redis는 Executor 이벤트 전달로 유지한다. 실제 분리 DB의 이관·기존 Worker drain·롤링 혼재 조건은 다음 단계에서 확인한다.

기존 실행 컨테이너·원본 checkout/.env·실제 서비스 DB는 변경하지 않았다. 검증용 PostgreSQL만 별도로 생성하고 검사 후 제거했다. 실제 사내 플랫폼·LLM·Executor 실연계 및 Kubernetes 용량 측정은 이번 검증으로 대체하지 않는다.
