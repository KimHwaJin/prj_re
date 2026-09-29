# 020 CRUD 실행 보호

상태: 구현·격리 PostgreSQL·전체 회귀·패키지 검증 완료 / 이번 항목 베이스 병합·배포 미수행 · feature/crud-execution-guards · 부모 d20872f

## 문제와 구현 범위

세션 삭제/프로젝트 이동, 프로젝트 삭제가 미종료 작업을 확인하지 않았다. 기본 프로젝트 DELETE는 하위 대화를 초기화해 합의된 거절 정책과 달랐다. 사용자 삭제는 보호가 있었지만 독립 이벤트 실행 소유권/복구 필요 플래그를 포함하지 않았다.

미종료 Task, 큐/실행 Run, Task 없는 과거 interrupt, 큐/실행 LLM, 실제 세션 실행 token/복구 플래그를 한 SQL snapshot에서 검사한다. 이름 변경은 허용하고 다른 프로젝트로의 이동/삭제는 409로 거절한다. 기본 프로젝트 DELETE는 항상 409다.

트랜잭션 순서: user FOR SHARE → UUID 순서의 project advisory lock → session admission advisory lock → 필요 시 Session row lock. 프로젝트 잠금은 일반 접수/생성/이동에서 공유, 프로젝트 삭제에서만 배타적이다. 그래프 실행 동안 유지하지 않는다. 동일 프로젝트의 서로 다른 세션을 직렬화하지 않는다.

Run 생성/resume, 세션 생성, 프로젝트 수정, 이벤트 Worker의 API 세션 점유 시작도 같은 짧은 잠금 경계에 참여한다. 공개 Message CUD는 아직 제거하지 않지만 상위 삭제와의 경합을 막는 잠금만 연결한다. 이미 접수된 실행은 durable Task/owner 상태로 보호한다.

## 검증 계획

격리 PostgreSQL에서 상태별 거절/허용, 동시 접수 대 삭제의 양방향 순서, resume 대 이동, 프로젝트 삭제 대 신규 세션/세션 유입, 반대 방향 동시 이동, 부모 비활성화 후 요청 거절, 이벤트 점유 대 삭제 및 기존 사용자 삭제 회귀를 확인한다. 다른 세션의 접수가 잠금 대기에 함께 묶이지 않는지 제어된 transaction으로 검증한다.

새 migration/설정 추가 없음. 조회 성능 개선·Message/Tasks 공개 API 제거·운영 복구는 후속이다. 원래 checkout/기존 서비스는 수정하지 않는다.

## 검증 결과

- 최초 집중 검증: **69 passed, 33.78초**.
- 전체 회귀: **442 passed, 2 subtests passed, 0 skipped, 43 warnings, 142.47초**. 기존 checkpointer 없는 그래프의 durability 경고다.
- 추가 경계 시나리오 4개를 포함한 최종 집중 검증: **73 passed, 35.22초**. 전체 실행에 포함된 69개와 중복되므로 442와 73을 합산하지 않는다.
- 최종 소스 wheel을 임시 경로에서 빌드한 뒤 `python -I`로 검증: 소스 checkout import 없이 API 34개 경로, 역할별 프롬프트 7개, Mock 그래프 execution step 6개 통과.
- `git diff --check` 통과. 실제 LLM·Executor·외부 Redis 연결 없음.

경합 검증은 단순 동시 호출의 우연한 순서에 기대지 않는다. DB transaction을 명시적인 gate에서 멈춘 뒤 다른 요청을 보내고, `pg_stat_activity`의 advisory wait와 `pg_blocking_pids`로 실제 잠금 대기를 확인한 후 첫 transaction을 진행시켰다.

| 검증 상황 | 확인한 결과 |
|---|---|
| 12종 상태 × 세션 삭제/이동·프로젝트 삭제·사용자 삭제 | 48개 모두 409, 이름/소속/삭제 여부/메시지 변경 없음 |
| 종료 Task의 과거 interrupted 구간 + 해제된 owner | 삭제/이동 가능, 과거 이력 때문에 영구 잠금되지 않음 |
| 실행/Executor 대기/복구 필요 중 이름 변경 | 허용, 같은 프로젝트로의 이동 요청은 무해한 no-op |
| 기본 프로젝트 DELETE | 409, 기존 대화 유지 |
| 삭제/이동이 먼저 잠금 획득 → 새 Run 접수 | 접수 대기 후 삭제는 404, 소속 변경은 409; Run 생성 없음 |
| Run 접수가 먼저 잠금 획득 → 삭제/이동 | 접수 commit 후 새 미종료 Task를 확인하여 409 |
| 프로젝트 삭제 → 신규 세션/다른 프로젝트에서 유입되는 이동 | 삭제 후 404, 비활성 프로젝트에 세션이 남지 않음 |
| 세션 생성 → 프로젝트 삭제 | 생성 commit 이후 하위 목록을 읽어 새 세션도 함께 soft delete |
| 이동 완료 → 원래 프로젝트 삭제 | 이동한 세션/메시지는 대상 프로젝트에 유지 |
| 반대 방향 동시 이동 | 교착 없이 둘 다 완료 |
| 동일 프로젝트의 서로 다른 세션 | 한 세션의 접수 잠금 중에도 다른 세션 접수 완료 |
| 미종료 Task가 과거 잘못 숨겨진 세션에 존재 | 프로젝트 삭제 거절 |
| Task 없는 독립 Executor owner가 API 세션을 점유 | 세션/프로젝트/사용자 삭제와 이동 거절; 실제 종료 후 허용 |
| 삭제가 먼저 → 늦은 Executor 이벤트 | graph 호출 전에 DeferEvent, 삭제된 API 자원 접근 방지 |
| 실제 Run Worker의 graph 대기 | 이름 변경 가능, 삭제/이동 거절, 완료 후 이동 가능 |
| 프로젝트 삭제 대 기존 Message 생성/수정 | 삭제 후 404, 늦은 활성 메시지 삽입/내용 갱신 없음 |
| HITL resume 접수 대 이동 | 동일 공개 Run으로 재개 접수, 이동은 409 |

[검증 요약 JSON](../reports/crud-execution-guards-validation-2026-09-29.json)과 [API 정책·개발 규칙](../crud-lifecycle-policy.md)을 함께 보존했다.

재현 명령: 전용 로컬 `identity_test` DB URL을 `DTEST_IDENTITY_TEST_DATABASE_URL`에 전달하고 `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest src -q`. 집중 검증은 `src/app/test/test_crud_guards_postgres.py`다. fixture가 전용 DB 스키마를 초기화하므로 서비스 DB를 지정하면 안 된다.

## 실제 변경 경계

- `resource_lifecycle.py`: user/project/session의 짧은 잠금 경계와 미종료/실행 소유권 검사. Task/Run 상태를 또 저장하는 별도 상태 머신은 추가하지 않는다.
- `SessionService`: 생성 시 상위 프로젝트 삭제와 직렬화; 이동·삭제 전 공통 검사. 이름 변경은 미종료 작업 검사에서 제외한다.
- `ProjectService`: 기본 프로젝트 DELETE 거절, 하위 미종료 작업 검사, 생성/수정·삭제 경합 보호. prompt_version 갱신도 row lock 아래 실행한다.
- `RunService`/`PublicRunService`: 접수/resume/cancel에서 같은 자원 잠금 순서를 사용한다. 이미 점유된 Worker 실행은 새 접수 잠금을 취하지 않고 기존 Task/owner 보호를 사용한다.
- `session_execution.run_event_owned`: API 소유 세션의 이벤트 점유 직전 상위 자원 활성 여부를 잠금 아래 재검사한다. API 세션이 없는 standalone graph는 기존 owner 프로토콜을 유지한다.
- `UserService`: 기존 사용자 배타 row lock을 보존하면서 Task·Run·LLM·owner 검사를 공통화한다. 종료 상태지만 recovery_required인 Task도 보호한다.
- `MessageService`: 상위 프로젝트 삭제/세션 이동 경합을 막는 잠금만 연결했다. 공개 Message CUD 제거는 수행하지 않았다.
- row lock 대기 후 세션 ORM 객체를 새로 읽도록 보완하고 대체된 미사용 `_require_user` helper를 제거했다.

## 적용 조건과 남은 제한

- **020 자체의 새 DB migration/환경변수는 없다.** 기반 019의 0021 migration 요구는 그대로다. 이번 코드를 운영 환경에 적용하지 않았다.
- 모든 관련 API/이벤트 writer가 같은 잠금 규칙을 사용해야 한다. 구 코드와 새 코드가 섞인 배포 구간이나 수동 SQL·우회 writer에 대해 이 보호를 보장하지 않는다. 배포 시 관련 쓰기를 멈추고 전체 writer 업데이트 후 다시 허용한다.
- PostgreSQL 기본 READ COMMITTED에서 독립 연결의 경합을 검증했다. Kubernetes 여러 Pod/실제 폐쇄망 환경에서의 배포 시험은 하지 않았다.
- 잠금은 DB transaction 동안만 유지한다. LLM/Executor 대기 동안 유지하지 않는 것은 기존 단일 풀/자원 수명 회귀로 재검증했다. 다만 대량 삭제 transaction이 오래 걸리면 같은 프로젝트의 새 접수는 기다릴 수 있다.
- 미종료/복구 필요 상태를 삭제로 우회하지 않는다. Executor 외부 취소 및 관리자 복구 API는 여전히 후속 작업이다.
- 추가 SQL·상위 자원 재검사가 있으므로 지연 개선 수치는 주장하지 않는다. 처리량 A/B나 대량 데이터 삭제 최적화는 수행하지 않았다.
- 단건 조회의 불필요한 하위 조회·Message CUD/Tasks 공개 API 정리·project_memory 저장은 후속이다. 내부 Message CUD로 실행 내용을 편집하는 기능까지 이번에 제거한 것은 아니다.
- 원래 checkout과 기존 서비스는 유지했다. 검증 전용 DB는 정리한다. 이전 019는 d20872f까지 베이스에 통합했고 이번 020은 파생 브랜치에 기록한다. 원격 push·운영 배포는 미수행이다.
