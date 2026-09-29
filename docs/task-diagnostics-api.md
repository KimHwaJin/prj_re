# Task 진단 API

022 구현 · 2026-09-29

사용자 실행의 기준은 공개 Run이다. Task는 내부 작업·세션 실행 점유를 조사하는 읽기 전용 진단 자원으로 유지한다. 내부 Task 테이블·이벤트·Worker 상태 관리 책임은 삭제하지 않는다. Message CUD는 이번 작업에서 변경하지 않는다.

## 경로와 권한

모든 경로 앞에 `/api/v1`을 붙이고 등록된 `X-User-Id`를 전달한다.

| 경로 | 응답 | 권한 |
|---|---|---|
| `GET /sessions/{session_id}/tasks` | `Page[TaskResource]` | 소유자, 활성 자원 |
| `GET /tasks/{task_id}` | `TaskResource` | 소유자, 활성 자원 |
| `GET /tasks/{task_id}/runs` | `Page[TaskInvocationResource]` | 소유자, 활성 자원 |
| `GET /admin/sessions/{session_id}/tasks` | `Page[TaskResource]` | 관리자, 타 사용자·숨김 자원 포함 |
| `GET /admin/tasks/{task_id}` | `TaskResource` | 관리자, 타 사용자·숨김 자원 포함 |
| `GET /admin/tasks/{task_id}/runs` | `Page[TaskInvocationResource]` | 관리자, 타 사용자·숨김 자원 포함 |

관리자여도 일반 `/tasks` 경로에서 타 사용자 자원을 볼 수는 없다. 일반 경로는 User·Project·Session의 활성 여부와 Session 소유권을 검사한다. 관리자 경로에는 별도의 admin 역할 검사가 있다. 미인증은 401, 관리자 경로의 일반 사용자는 403, 없는/비소유 자원은 404다. 관리자 조회는 물리적으로 존재하는 숨김 데이터를 읽을 뿐 복원하거나 실행 점유를 해제하지 않는다.

## Task 자체와 Session 현재 상태

- `status`: 내부 Task 저장 상태. `waiting_input`에는 사용자 승인 대기와 Executor 대기가 모두 포함될 수 있다. 사용자 화면의 구분은 공개 Run의 `status`를 사용한다.
- `is_unfinished`: 이 Task가 미종료 상태이거나 `recovery_required`인지 여부다. 기존 `is_active`는 제거했다.
- `public_run_id`: 명령·화면 조회에 사용할 안정적인 공개 Run ID. 연결이 없는 과거 Task는 null일 수 있다.
- `graph_task_id`, `root_run_id`, `checkpoint_run_id`: 내부 연결 진단값. 호출 대상을 임의로 조립하는 입력이 아니다.
- 기존 `lock_owner`, `heartbeat_at`, `lease_expires_at`는 Task lease의 기록이다. 현재 graph 소유권과 구분한다.
- `observed_at`: 해당 조회 SQL의 관측 시각이다.

`session_work`는 **조회한 Task만이 아닌 같은 Session 전체의 현재 상태**다. 과거 Task가 성공했어도 다른 작업이 미종료이면 새 입력 가능으로 표시하지 않는다.

| 필드 | 의미 |
|---|---|
| `resources_active` | User·Project·Session이 모두 활성인지 |
| `has_unfinished_work` | 미종료/복구 필요 Task, 큐·실행 Run, Task 없는 interrupt, 큐·실행 LLM, 실행 점유/종료 불명 중 하나라도 있는지 |
| `can_start_new_run` | 활성 자원이고 위 차단 사유가 없을 때의 보수적인 새 일반 입력 가능 snapshot |
| `blocking_reasons` | `resources_inactive`, `unfinished_task`, `unfinished_run`, `unfinished_llm`, `execution_held_or_uncertain` 중 해당 사유 |
| `execution.ownership_held` | `session_executions`에 점유 token이 남아 있는지. token 자체는 노출하지 않음 |
| `execution.owner_kind/owner_id/owner_process` | 현재 점유/복구 확인 대상의 종류·내부 ID·프로세스 |
| `execution.acquired_at/heartbeat_at` | 점유·마지막 heartbeat 기록 시각 |
| `execution.recovery_required/recovery_reason` | 종료·쓰기 권한 확인이 필요한 상태와 기록 사유 |

점유가 해제되고 복구 플래그도 없으면 오래된 owner metadata는 응답에서 null로 표시한다. token이 없어도 recovery_required이면 새 입력 가능으로 표시하지 않는다. heartbeat가 8일 전이어도 token이 남았다면 그대로 점유 중으로 보고하며, 진단 GET은 자동 만료·재실행·복구를 수행하지 않는다. heartbeat가 최근이라는 사실만으로 프로세스 생존을 확정하는 필드도 제공하지 않는다.

`can_start_new_run`은 HITL 응답 가능 여부가 아니다. 사용자 승인 대기에서도 false이며 현재 Run의 token으로만 resume한다. 조회는 예약/접수 보장이 아니므로 프론트가 읽은 직후 다른 요청이 들어오거나 서버가 drain/과부하 상태이면 실제 POST가 거절될 수 있다. 최종 판정은 명령 API가 한다. 관리자 조회는 작업 종료·복구 API가 아니다.

## 페이지와 내부 실행 구간

두 목록 모두 `{ "items": [...], "page": { "has_next": true, "next_cursor": "..." } }` 형태다. `limit`은 기본 50, 최대 200이고 `cursor`, `sort=created_at|-created_at`, `created_at_from`, `created_at_to`를 받는다. 기본 정렬은 최신순이며 같은 시각이면 UUID로 순서를 결정한다. 날짜 범위는 `[from, to)`다.

`/tasks/{task_id}/runs`는 내부 호출 구간 이력이다. `invocation_id`는 재개마다 달라지고, 같은 전체 작업의 `public_run_id`는 유지된다. 구간별 status·시작/완료 시각·시도 횟수·실패를 제공한다. 사용자 입력·명령·전체 metadata·Executor 요청·원본 결과·점유 token은 이 응답에 넣지 않는다. 전체 Run 상태는 `/sessions/{session_id}/runs/{public_run_id}`에서 읽는다.

## 클라이언트 전환

- `/tasks/{task_id}/resume`, `/cancel`, `/stream`은 제거되어 404다. 동일 기능은 `/sessions/{session_id}/runs/{public_run_id}/resume|cancel|stream`으로 호출한다. HITL token/멱등 키 계약은 [Run API](public-run-api.md)를 따른다.
- Task 목록의 기존 배열 응답은 `items`로 접근한다. 실행 구간 목록도 `items`와 cursor를 사용한다.
- 실행 구간의 이전 `id`는 `invocation_id`로 명확히 구분한다. 공개 Run ID로 오인하지 않는다.
- `is_active`로 입력/버튼 활성화를 판단하지 않는다. 새 입력은 Session snapshot, 재개·취소는 Run 계약을 따른다.
- 운영 프론트·현재 부하 시나리오는 Runs를 기준으로 한다. 레거시 내부 demo는 이번 호환 범위가 아니며 제거한 Task 명령과 구 배열/필드를 사용하는 부분은 별도 전환이 필요하다.

새 DB migration/설정은 없다. Task가 없는 세션의 별도 운영 진단·복구, 검색 조건이 있는 전역 작업 대시보드, 장기 이력 보존/삭제는 후속 범위다. 이 API를 사용자 화면의 추가 폴링 대상으로 요구하지 않는다.
