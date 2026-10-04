# CRUD 실행 보호 정책

020 구현 · 2026-09-29 / 076 세션 이동 제거 · 2026-10-04

## 클라이언트 동작

| API/행위 | 허용 조건 |
|---|---|
| DELETE /api/v1/sessions/{id} | 해당 세션에 미종료 작업·미확인 실행 소유권이 없어야 함 |
| PATCH /api/v1/sessions/{id} | session_name 변경만 지원. project_id/target_project_id 등 알 수 없는 필드는 422 |
| DELETE /api/v1/projects/{id} | 기본 프로젝트가 아니며 모든 하위 세션의 작업/소유권이 종료되어야 함 |
| DELETE /api/v1/users/{user_id} | 기존 관리자/마지막 관리자 보호 + 소유 자원의 모든 작업/소유권 종료 |
| 세션/일반 프로젝트 이름 변경 | 실행 중에도 허용; 기존 이름 검증·기본 프로젝트 이름 제한 유지 |
| 같은 사용자의 다른 세션 생성/실행 | 허용; 한 세션의 장기 실행 때문에 프로젝트 전체를 실행 잠금하지 않음 |

미종료에는 큐 대기·실행 중·HITL 응답 대기·Executor 결과 대기가 포함된다. `recovery_required`이거나 세션 실행 token이 아직 남아 종료를 확인하지 못한 경우도 삭제를 거절한다. 거절은 HTTP 409이며 데이터를 일부 수정하거나 자동 취소하지 않는다.

기본 프로젝트 DELETE는 **항상 409**다. 하위 대화 전체 삭제로 해석하지 않는다. 사용자 삭제에 따른 소유 데이터 cascade는 별도 정책으로 유지한다. 없는 자원/타 사용자 자원/비활성 부모는 404로 처리한다.

세션의 프로젝트 소속은 생성할 때 고정하며 공개 API로 이동하지 않는다. PATCH에서 소속 변경 필드와 이름 변경을 함께 보내도 전체 요청을 422로 거절하고 이름을 부분 반영하지 않는다. Executor 장기 대기나 복구 필요 상태에서 삭제 요청을 반복한다고 제한이 해제되지는 않는다.

삭제는 기존 soft delete다. 종료 이력의 과거 interrupted 실행 구간은 삭제를 막지 않는다. 오래된 Task 없는 interrupted 기록은 아직 재개 가능한 미종료 작업으로 취급한다.

## 서버 개발 규칙

- 새로운 접수/생성/삭제 경로는 `api_service.services.resource_lifecycle`의 경계에 참여한다. 단순 `SELECT status` 검사만 한 뒤 commit하는 방식은 경합에 안전하지 않다.
- 잠금 순서는 user FOR SHARE → 정렬된 project advisory lock → session admission advisory lock → 필요한 Session row lock이다. 프로젝트 잠금은 보통 공유, 프로젝트 삭제만 배타적으로 취한다.
- 잠금 대기 뒤 활성 여부/프로젝트 소속을 다시 읽는다. 다른 프로젝트로 바뀌었으면 잠금 순서를 깨며 추가 잠금을 취하지 않고 409로 끝낸다.
- 삭제 전 `require_idle`을 검사하고 같은 transaction에서 변경·commit한다. 이름 변경에는 미종료 작업 검사를 적용하지 않는다. 검사는 Task·Run·LLM·session_executions를 한 SQL snapshot에서 읽는다.
- Worker Run/Task row lock을 기다리면서 CRUD 자원 잠금을 잡는 방식을 추가하지 않는다. 이미 실행 중인 구간은 영속 상태로 보호하고 짧은 검사 후 거절한다.
- 접수 commit 이후 그래프·LLM·Executor 호출 동안 자원 잠금이나 서비스 DB 연결을 유지하지 않는다.
- API 세션의 Executor 이벤트 점유도 이 경계를 거친다. 이미 삭제된 자원에 대한 이벤트는 그래프를 호출하지 않고 보류한다. 영구적으로 유효하지 않은 이벤트의 격리/정리 정책은 후속 운영 범위다.
- 공개 Message CUD가 남아 있는 동안 해당 경로도 부모 삭제와 직렬화한다. 해당 API들의 최종 제거는 별도 작업이다.

## 배포 범위

020은 새 migration/설정을 요구하지 않는다. 기반 019의 0021 migration이 필요한 환경이라면 먼저 그 절차를 따른다. 관련 API/이벤트 writer 모두 같은 코드를 사용해야 하므로 쓰기를 중지한 상태에서 전체 업데이트 후 재개한다. 구/신 writer 혼합 구간, 수동 SQL, 외부 우회 writer는 이 애플리케이션 잠금 규칙의 보장 밖이다.

[문제·구현·검증·제한 기록](improvements/020-crud-execution-guards.md)

[세션 이동 제거·검증 기록](improvements/076-session-project-fixed.md)

세션 생성 설정은 [세션 API·커널 계약](session-api.md)을 따른다.


세션 응답의 대화 입력 능력은 [세션 API](session-api.md#현재-run과-대화-입력-가능-여부)를 따른다. 078에서 새 Run/사용자 resume도 Task만 검사하지 않고 명령·현재 Run·LLM·실행 점유·복구를 함께 검사한다. HITL의 응답 가능과 삭제 가능은 다르다. Task 없는 과거 interrupt는 같은 공개 Run의 최신 호출로 교체되었으면 미종료 Run으로 계산하지 않는다. 이러한 최신 호출 판정은 삭제/Task 진단/입력 판정에 공통 적용한다.


프로젝트 목록·상세 분리, 생성·수정 검증 및 지침의 새 Run/재개 적용 시점은 [프로젝트 API](project-api.md)를 따른다. 기본 프로젝트 이름/삭제 금지와 실행 중 삭제 보호는 유지한다.
