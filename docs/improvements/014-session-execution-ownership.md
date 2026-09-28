# 014 — API Run·Executor 이벤트의 공통 세션 실행 소유권

- 일자: 2026-09-29
- 구현 브랜치: `feature/refactor-session-ownership`
- 출발 commit: 013 `70e1c9b` (`feature/refactor-run-concurrency`)
- 베이스: `feature/refactor-base`는 012 `fb89dbc`. 이번 요청은 014 구현이며 013/014의 베이스 병합은 수행하지 않았다.
- 상태: 구현·격리 PostgreSQL·패키지 검증 완료. 베이스 병합·원격 push·서비스 배포 미수행.

## 문제와 선택

기존 API Run Worker는 PostgreSQL Task 토큰/lease를, Executor 이벤트 Worker는 Redis SessionGuard를 사용했다. API bridge에도 Redis guard 객체가 있었지만 실제 그래프 호출에서 사용되지 않았다. 따라서 양쪽이 같은 세션 그래프를 실행하지 않도록 배제하는 공통 실행 소유권이 없었다. 013의 사용자 입력 차단·이벤트 완료 상태 반영은 이 소유권을 대신하지 않는다. 이번 조사로 모든 정상 요청에서 중복 실행이 재현되었다는 뜻은 아니다.

이번에는 `DATABASE_URL`이 가리키는 API DB에 **session_executions** 테이블을 추가했다. API Run/Executor 이벤트 모두 동일한 세션 UUID의 소유권을 획득한 뒤 실행한다. PostgreSQL transaction 동안만 연결을 빌리고, 모델/그래프가 기다리는 전체 시간 동안 행 잠금이나 DB 연결을 붙잡지 않는다.

**heartbeat가 오래되었다는 이유만으로 다른 실행자가 토큰을 덮어쓰지 않는다.** 네트워크 단절이나 Pod 정지 시 이전 그래프가 실제로 멈췄는지 확인할 수 없기 때문이다. 정상 반환은 소유권을 해제하지만 불확실한 종료는 토큰을 남겨 중복 실행을 막는다. 이 선택은 자동 복구/가용성보다 중복 쓰기 방지를 우선하며, 복구 필요 상태의 자동 재개를 구현했다는 의미가 아니다.

## 실행 경계

| 경로 | 소유권 확보 | 소유권 반환 |
|---|---|---|
| API Run | queue의 Run/Task 점유와 같은 transaction에서 토큰 확보 | 그래프·메시지 저장·Run/Task 결과 반영·실행 정리가 모두 반환된 뒤 |
| Executor 이벤트 | adapter 호출 전 같은 DB 테이블에서 토큰 확보 | adapter의 graph resume와 API 완료 상태 반영이 반환된 뒤 |
| HITL/Executor 대기 | interrupt까지 실행하는 동안만 점유 | 호출 반환 후 실행 토큰 해제, Task의 논리적 세션 잠금은 유지 |
| 취소·소유권 상실·감시 실패 | 새 실행 시작 금지 | 자동 반환하지 않고 recovery_required와 기존 토큰 보존 |
| 프로세스 강제 종료·commit 결과 불명 | 토큰이 commit되었다면 다른 실행자가 점유 불가 | 기존 프로세스 종료 확인과 상태 검토 후 운영 복구 필요 |

`acquire()`는 토큰이 없고 recovery_required가 false인 행만 원자적으로 점유한다. 토큰은 TTL로 만료되지 않는다. 과거 heartbeat는 관측 자료이며 재점유 허가가 아니다. 토큰 소유자는 동일 토큰 조건으로만 갱신/해제/복구 필요 표시를 할 수 있다. 강제 변경된 다른 토큰을 지우지 않는다.

API Worker는 점유된 세션을 queue 조회에서 제외한다. 맨 앞 세션이 이벤트 처리 중이라는 이유로 다른 세션의 pending Run까지 막지 않는다. 경합으로 실제 소유권 확보에 실패하면 Run/Task 변경을 rollback하고 실행하지 않는다. API 접수 때 recovery_required 세션에는 409를 반환하며, 같은 idempotency key의 기존 응답 반환 정책은 유지한다.

이벤트는 기존 Task가 pending/running/recovery_required인 경우에도 defer한다. 이후 shared owner를 확보하지 못하면 graph 조회·resume·API 결과 반영을 호출하지 않는다. 이벤트의 업무상 중복 판정은 기존 command 상태·receipt·sequence 검사를 유지한다. 소유권만으로 동일 이벤트를 영구적으로 deduplicate한다고 해석하지 않는다.

## 감시와 종료

공통 소유권 서비스는 실행 작업과 감시 작업을 추적한다. 실행 직전 토큰을 확인하고, 실행 중 heartbeat를 짧은 transaction으로 기록한다. 간격은 기존 `task_lease_seconds / 3`을 사용하며, 이것이 토큰 TTL을 의미하지는 않는다. DB 작업에는 기존 `run_monitor_timeout_seconds`가 적용된다. 별도 설정 키는 추가하지 않았다.

소유권 갱신 실패·취소·실행 종료 불확실성에서는 프로세스 execution_health를 실패로 표시하고 세션을 복구 필요로 기록한다. 작업 종료가 확인될 때까지 자식 실행과 감시를 정리한다. API Worker는 더 이상 새 Run을 점유하지 않고, 이벤트 경로도 새 실행을 거절한다. 이벤트 Worker readiness에 동일한 건전성 확인을 추가했다.

이벤트 소유권을 commit하는 중에 상위 작업이 취소되어도 해당 handoff가 끝날 때까지 확인한다. 이미 확보된 토큰으로 새 그래프를 시작하지 않고 복구 필요로 남긴다. API queue의 기존 commit/handoff 보호도 유지한다. DB 장애로 복구 표시를 쓰지 못하더라도 기존 토큰은 남기므로 다른 Worker가 자동으로 가져가지 못한다.

Redis SessionGuard와 메시지 lease가 실패하면 이벤트 실행에 취소가 전달된다. 그 상황에서도 공통 DB 소유권을 조기에 해제하지 않는다. Redis ingress/dispatch/outbox와 소비 그룹은 그대로 유지하며, API bridge의 **사용되지 않던** Redis client/guard 생성 코드만 제거했다.

## DB 설정과 배포

새 migration은 `crud_migrations/versions/20260929_0020_session_execution.py`다. CRUD Alembic으로 생성하며 LangGraph checkpoint 테이블이나 이벤트 Inbox/Outbox 테이블을 옮기지 않는다.

```sh
python -m alembic -c alembic.crud.ini upgrade head
```

이는 배포 시 수행할 명령이며 기존 서비스 DB에 이번 작업으로 실행하지 않았다. 실제 upgrade/downgrade 검증은 일회용 identity_test DB에만 수행한다.

모든 API Run Worker와 이벤트 Worker가 **같은 DATABASE_URL의 session_executions**를 봐야 한다. 이벤트 Worker의 EW_DATABASE_URL은 여전히 Inbox/Outbox·binding 저장소이고, CHECKPOINT_DB_URI는 checkpointer 저장소다. 세 URL을 같은 DB로 통합할 필요는 없지만 같은 역할의 모든 실행자가 공통 저장소를 보아야 한다.

첫 전환에서 014 이전 Worker는 이 테이블을 존중하지 않는다. **기존 그래프 실행을 배수하고 모든 이전 Worker를 중지한 상태에서 migration/코드 전환을 수행해야 한다.** 이전·새 Worker를 동시에 실행하는 무중단 rolling upgrade의 안전성을 이번 변경만으로 보장하지 않는다. downgrade 역시 활성 실행을 중단·확인한 뒤 수행해야 한다. 장기 Executor 대기 세션은 실행 중인 코루틴과 다르므로 Executor 작업 자체를 일주일 동안 기다려야 한다는 의미는 아니다.

## 운영 확인과 복구 제한

다음은 읽기 전용 진단 예시다. 복구 작업이나 잠금 삭제 명령이 아니다.

```sql
SELECT session_id, owner_kind, owner_id, owner_process,
       acquired_at, heartbeat_at, recovery_required, recovery_reason
FROM session_executions
WHERE token IS NOT NULL OR recovery_required
ORDER BY acquired_at;
```

행은 독립 Agent 세션도 보호하므로 API sessions에 FK를 걸지 않는다. 정상 종료 후 행은 남고 token만 null이 된다. 행 보존/정리 정책은 별도 후속이다. API 소유자는 Run ID, 이벤트 소유자는 command ID로 원인을 추적한다.

복구 전에 이전 Pod/프로세스와 그 자식 I/O가 실제로 종료되었는지 확인해야 한다. 이후 Task/Run 상태, 최신 checkpoint, 이벤트 receipt, 이미 제출한 Executor 요청의 멱등 키/접수 결과를 함께 확인해야 한다. 토큰만 지우거나 recovery_required만 false로 바꾸는 것을 일반적인 복구 절차로 제공하지 않는다. 자동 복구 API/CLI와 재시작 후 자동 인계는 이번 범위에서 구현하지 않았다.

DB 토큰을 강제로 바꾼 뒤 살아 있는 구 Writer와 신규 Writer를 동시에 실행하는 상황에 대한 **checkpoint별 원자적 fencing은 아니다.** 정상 코드에는 활성 토큰을 뺏는 경로가 없으며, 소유권 상실 감시는 주기적이다. 외부 관리자가 토큰을 강제로 바꾼 순간 진행 중인 체크포인트/HTTP 요청을 원격 취소하는 보장은 없다. 새로운 Graph writer를 추가할 때도 공통 소유권 경계를 통과해야 한다. 개발용 CLI의 직접 graph 호출을 운영 API DB 세션에 병행하면 이 계약 밖이다.

## 검증 결과

최종 전체 회귀: **325 passed, 2 subtests passed, 0 skipped**, 60.95초. 43개 경고는 기존 LangGraph의 checkpointer 없는 내부 Agent durability 경고다. 신규 검증은 PostgreSQL 14개와 이벤트 Dispatcher 오류 분류 3개다. 새 migration의 upgrade/downgrade도 기존 격리 DB fixture에서 실행했다.

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
DTEST_IDENTITY_TEST_DATABASE_URL=<격리된 로컬 identity_test DB URL> \
python -m pytest src/app/test src/agent_service/agents/analysis/tests -q
```

새 wheel을 checkout 밖의 임시 디렉터리에서 만들고 소유권 서비스/모델의 포함을 확인했다. `python -I scripts/diagnostics/validate_agent_package.py <wheel>` 격리 검증에서 OpenAPI 33개 경로, 7개 역할 Agent 구성/프롬프트, mock graph 6개 실행 step과 workflow 자산을 확인했다. Agent 업무 로직·프롬프트·workflow 자산은 이번에 변경하지 않았다. [검증 요약 JSON](../reports/session-execution-validation-2026-09-29.json)을 함께 기록한다.

테스트 중 모의 Redis가 그래프 시작 전 소유권을 잃게 만들어 테스트 자체가 시작 신호를 무기한 기다리는 경쟁이 발견됐다. 그래프 진입 후 갱신 실패를 주입하도록 수정하고 최종 전체 검증을 재실행했다. 운영 장애 재현이나 실제 Redis 장애 시험으로 집계하지 않는다.

새 테스트 파일은 `src/app/test/test_session_execution_postgres.py`다. 다음을 실제 PostgreSQL에서 검증한다.

- API 결과가 DB에 저장됐지만 실행 코루틴 정리가 남은 동안 이벤트 진입 차단
- 이벤트 점유 세션을 건너뛰고 다른 세션 Run 점유, 이후 해당 세션 정상 인계
- 8개 동시 이벤트 시도의 중복 실행 차단
- 8일 지난 heartbeat로도 토큰 탈취 금지
- 취소·반복 취소·느린 정리 중 토큰 유지
- 소유권 강제 변경 감지, 구 소유자의 새 토큰 해제 금지
- 이벤트 점유 transaction 중 취소와 commit 이후 격리
- 정상적으로 반환된 defer 오류 뒤 receipt 재처리 기회 보존
- 모의 Redis 갱신 실패 중 DB 소유권 유지
- 그래프 대기 동안 공통 소유권용 DB 연결 반환
- 복구 필요 세션의 API 409
- 소유권 heartbeat DB timeout 시 그래프 중단/소유권 보존
- 기존 API Task가 실행/복구 중이면 owner 행이 없어도 이벤트 거절
- 실제 PostgreSQL checkpointer의 checkpoint가 소유권 이전 전 변경되지 않고, 인계 후 정상 resume

Redis 장애는 제어 가능한 모의 클라이언트로 주입한다. LLM/Executor 실제 호출, 실제 Redis 서버의 장애, 다중 Kubernetes Pod, 실제 강제 종료/재시작 자동 복구, 1주 경과 시험은 수행하지 않았다. 8일 지난 timestamp 테스트를 8일 운영 시험으로 해석하지 않는다. 이번 변경의 성능 향상 수치는 주장하지 않는다.


구현과 이 기록은 같은 작업 commit에 포함한다. 검증용 PostgreSQL 컨테이너는 검증 후 제거하며 기존 서비스 컨테이너/외부 DB는 수정하지 않는다. 이 기록은 자동 복구나 운영 배포 완료를 뜻하지 않는다.
