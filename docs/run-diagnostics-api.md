# Run 진단 조회 API

081 구현 · 2026-10-04. 공개 Run을 기준으로 내부 Task와 실행 구간을 조회한다. 별도 Task 공개 자원/목록은 제공하지 않는다. 내부 Task 테이블·Worker·세션 점유·checkpoint·TaskEvent 저장은 유지한다.

## 경로와 권한

모든 경로 앞에 `/api/v1`을 붙인다. 실제 서비스 인증은 SSO 로그인 쿠키이며 X-User-Id나 body.user_id로 대체하지 않는다. GET에는 CSRF 헤더를 요구하지 않는다.

| 경로 | 응답 | 권한 |
| --- | --- | --- |
| GET /sessions/{session_id}/runs/{run_id}/diagnostics | RunDiagnosticsResource | 소유자, 활성 User·Project·Session |
| GET /sessions/{session_id}/runs/{run_id}/invocations | Page[RunInvocationResource] | 소유자, 활성 자원 |
| GET /admin/sessions/{session_id}/runs/{run_id}/diagnostics | RunDiagnosticsResource | 관리자, 타 사용자·소프트 삭제된 자원 포함 |
| GET /admin/sessions/{session_id}/runs/{run_id}/invocations | Page[RunInvocationResource] | 관리자, 타 사용자·소프트 삭제된 자원 포함 |

관리자여도 일반 경로의 소유권을 우회하지 않는다. 미인증/비활성 로그인 사용자는401, 관리자 경로의 일반 사용자는403, 없는·비소유·세션 불일치 Run은404다. 물리 삭제된 리소스를 복구/재생하는 API가 아니다. 정상 클라이언트는 공개 run_id를 사용하며 기존 내부 invocation ID로 조회해도 공개 ID로 해석한다. 요청 invocation과 공개 root 모두 동일 세션이고 root가 canonical인지 확인한다.

## 사용자 화면과 진단 화면

- Run 목록: 공개 Run 요약으로 작업을 찾는다. Task 목록은 따로 조회하지 않는다.
- Run 상세·SSE: 사용자에게 보여줄 상태·결과·HITL·중간 메시지·재접속이다.
- diagnostics: 최신 invocation에 연결된 Task 상태와 같은 세션 전체의 현재 점유·차단 원인을 조사한다.
- invocations: 처음 호출·각 HITL resume 등 내부 실행 구간 이력을 조사한다. 오래된 Task에 연결된 과거 구간과 Task 없는 과거 구간도 같은 공개 Run이면 포함한다.
- logs: Agent/node별 저장된 진단 payload다. 기존 owner-scoped 정책을 유지하며 이번에 admin logs를 추가하지 않는다.

Task는 내부 상태이며 별도 사용자 실행 단위가 아니다. 정상적인 공개 Run은 하나의 Task를 공유하지만 DB에 강제된1:1 제약을 새로 추가하지 않는다. 과거 데이터에서 다른 Task로 이어진 경우 diagnostics는 최신 invocation의 Task만, invocations는 전체 공개 Run 이력을 반환한다.

## diagnostics 응답

진단의 run_id/session_id/observed_at/task/session_work를 한 DB statement snapshot에서 읽는다. Task가 없는 과거 Run은200 + task:null이다. 연결된 Task가 다른 세션이나 다른 공개 root를 가리키면 그 Task 본문은 노출하지 않는다. root_run_id가 없는 과거 Task는 같은 세션의 최신 invocation 연결로 읽을 수 있다. 조회한 Run이 종료했어도 같은 세션의 다른 작업이 실행 중일 수 있다.

### RunDiagnosticsResource

| 필드 | 의미 |
| --- | --- |
| run_id | HITL 재개 전후에 유지되는 공개 Run ID. 내부 invocation_id와 구분한다. |
| session_id | 이 공개 Run이 속한 대화 세션 UUID. |
| observed_at | 이 진단 SQL statement의 DB 관측 시각. 실행 완료나 heartbeat 시각이 아니다. |
| task | 최신 실행 구간에 연결된 내부 Task 진단 정보. 연결이 없거나 다른 Session/Run이면 null이다. |
| session_work | 해당 Run뿐 아니라 같은 세션 전체의 현재 미완료 작업·점유 진단이다. |

### TaskDiagnostics

| 필드 | 의미 |
| --- | --- |
| task_id | 내부 Task 레코드 UUID. API 조회 주소는 공개 run_id를 사용한다. 과거 Task 없는 invocation은 null이다. |
| graph_task_id | Agent 그래프에서 사용하는 분석 작업 ID. 서비스 task_id와 별개다. |
| root_run_id | Task에 기록된 최초 실행 구간 ID. |
| checkpoint_run_id | Task에 기록된 LangGraph checkpoint 기준 ID. 진단값이며 API 경로를 조립하는 값이 아니다. |
| trigger_message_id | 내부 Task를 시작하게 한 메시지 UUID. |
| trigger_type | 내부 Task의 실행 계기 분류 문자열. |
| status | 내부 Task 상태. waiting_input에는 사용자 승인·Executor 대기가 모두 포함될 수 있다. |
| is_unfinished | 이 Task가 미종료 상태이거나 복구 확인이 필요한지. 세션 전체 상태와 별개다. |
| lock_owner | Task lease에 기록된 소유자. 현재 그래프 점유는 session_work.execution에서 확인한다. |
| heartbeat_at | 마지막으로 기록된 heartbeat 시각. 오래됐다는 이유만으로 종료나 미점유를 확정하지 않는다. |
| lease_expires_at | 기록된 Task lease 만료 시각. 세션 점유 해제나 재실행 허가를 뜻하지 않는다. |
| cancel_requested_at | 취소 요청이 기록된 시각. 실제 종료 확인과 별개다. |
| failure_reason | Task에 기록된 내부 실패 사유. |
| recovery_required | Task 또는 실행 점유에 대한 복구 확인 필요 여부. 이 조회는 복구를 실행하지 않는다. |
| created_at | 해당 Task 또는 invocation 레코드의 DB 생성 시각. |
| updated_at | 해당 Task 또는 invocation 레코드의 마지막 갱신 시각. |
| completed_at | 해당 Task 또는 invocation의 기록된 종료 시각. invocation 종료는 공개 Run 전체 완료와 다르다. |

### SessionWorkDiagnostics

| 필드 | 의미 |
| --- | --- |
| resources_active | 연결된 User·Project·Session이 모두 활성인지. 관리자만 숨김 자원도 조회한다. |
| has_unfinished_work | 세션 전체에 미종료 작업·복구 필요·점유 또는 종료 불명이 존재하는지. |
| can_start_new_run | 새 일반 입력에 대한 보수적 진단 snapshot. HITL 재개 권한이나 예약이 아니며 UI는 세션 availability, POST는 실제 재검사를 사용한다. |
| blocking_reasons | 세션 전체의 새 일반 입력 차단 원인 목록. 사용자용 availability.reason과 별개의 내부 진단이다. |
| execution | 같은 세션의 현재 실행 점유 기록. 조회한 Run과 다른 Run의 점유일 수도 있다. |

### SessionExecutionDiagnostics

| 필드 | 의미 |
| --- | --- |
| ownership_held | DB에 세션 실행 점유가 기록되어 있는지. 워커의 실제 생존을 입증하지 않는다. |
| owner_kind | 기록된 점유 종류. api_run 또는 executor_event 등. |
| owner_id | 기록된 점유자 ID. 공개 Run 조회 경로를 조립하는 값이 아니다. |
| owner_process | 점유자로 기록된 프로세스 식별 문자열. |
| acquired_at | 기록된 세션 실행 점유 획득 시각. |
| heartbeat_at | 마지막으로 기록된 heartbeat 시각. 오래됐다는 이유만으로 종료나 미점유를 확정하지 않는다. |
| recovery_required | Task 또는 실행 점유에 대한 복구 확인 필요 여부. 이 조회는 복구를 실행하지 않는다. |
| recovery_reason | 세션 실행 점유 복구 확인이 필요한 것으로 기록된 사유. |

`session_work`는 해당 Run의 Task만 보는 값이 아니다. `can_start_new_run`은 진단용 보수적 snapshot이며 HITL 승인 가능 여부나 입력 자리를 예약하는 값이 아니다. 프론트 입력 제어는 [세션 availability](session-api.md)를 사용하고 POST에서 재검사한다. 해제된 점유의 옛 owner 메타는 현 소유자로 표시하지 않으며 복구 필요 기록은 유지한다. heartbeat/lease 시각만 보고 종료·자동 복구·잠금 해제를 수행하지 않는다. lock_token과 실행 점유 token은 반환하지 않는다.

## invocations 응답과 페이지

items와 page.has_next/page.next_cursor를 반환한다. 기본 limit50, 범위1~200, sort=-created_at 또는 created_at, cursor, 날짜 created_at_from 이상/created_at_to 미만을 지원한다. 동시각은 invocation_id로 정렬하며 후속 페이지에 같은 정렬·날짜 조건을 사용한다. 총 개수는 계산하지 않는다. cursor는 페이지 위치이며 SSE Last-Event-ID와 다르고 계속 갱신되는 데이터의 고정 snapshot을 보장하지 않는다.

| 필드 | 의미 |
| --- | --- |
| invocation_id | 최초 실행·각 resume마다 생성되는 내부 실행 구간 UUID. 공개 Run ID와 다르다. |
| run_id | HITL 재개 전후에 유지되는 공개 Run ID. 내부 invocation_id와 구분한다. |
| task_id | 내부 Task 레코드 UUID. API 조회 주소는 공개 run_id를 사용한다. 과거 Task 없는 invocation은 null이다. |
| session_id | 이 공개 Run이 속한 대화 세션 UUID. |
| status | 내부 실행 구간 상태 pending/running/interrupted/success/error/timeout/canceled. 공개 Run의 집계 상태가 아니다. |
| attempt_count | 해당 invocation을 Worker가 점유한 횟수. 최초 시도도 포함한다. |
| next_attempt_at | 해당 invocation을 다시 점유할 수 있는 다음 재시도 시각. |
| cancel_requested_at | 취소 요청이 기록된 시각. 실제 종료 확인과 별개다. |
| cancel_reason | 해당 invocation에 기록된 취소 사유. |
| failure | 해당 실행 구간에 기록된 실패 객체. 생산자가 정한 진단 형식이며 공개 Run 최종 결과가 아니다. |
| created_at | 해당 Task 또는 invocation 레코드의 DB 생성 시각. |
| updated_at | 해당 Task 또는 invocation 레코드의 마지막 갱신 시각. |
| started_at | 해당 invocation의 실제 실행 시작 기록 시각. |
| completed_at | 해당 Task 또는 invocation의 기록된 종료 시각. invocation 종료는 공개 Run 전체 완료와 다르다. |

각 구간의 failure는 기존 진단 객체를 유지한다. 레코드 수 상한이며 바이트 상한은 아니다. 사용자 입력·재개 command·전체 metadata·Executor 요청·원본 Agent 결과·interrupt 본문은 읽거나 반환하지 않는다. 모든 구간의 run_id는 같은 공개 ID다.

[진단 응답 예제](contracts/agent-api/responses/run_diagnostics.json) · [필드 주석](contracts/agent-api/responses/run_diagnostics.jsonc) · [실행 구간 응답 예제](contracts/agent-api/responses/run_invocations.json) · [필드 주석](contracts/agent-api/responses/run_invocations.jsonc). 예제는 설명용 ID/시각이며 실제 서비스 실행 기록이 아니다.

## 기존 Task API에서 변경

기존 일반/관리자 `/sessions/{session_id}/tasks`, `/tasks/{task_id}`, `/tasks/{task_id}/runs`는 모두 제거되어404다. Task 목록 대신 공개 Run 목록, Task 상세 대신 해당 Run의 diagnostics, Task의 runs 대신 invocations를 사용한다. 별칭·리다이렉트는 남기지 않는다. task_id만 저장한 클라이언트는 공개 Run 목록/상세 또는 기존 Task.root_run_id 연결을 확인하여 session_id/run_id를 확보해야 한다. 실제 외부 관리 화면의 이행 확인은 별도 통합 범위다.

독립적으로 남은 Task인데 연결된 공개 Run이 없는 과거 데이터는 이 API로 조회하지 않는다. 관리자용 orphan Task 탐색 기능을 이번 범위에 추가하지 않는다. Task resume/cancel/stream도 없으며 사용자 새 입력·resume는 기존 Run POST, 취소·구독은 Run cancel/stream을 사용한다. Message CUD·Workflow CRUD 후순위는 유지한다.

## 조회 비용과 변경 범위

진단 GET은 인증+snapshot2 SELECT, invocation 페이지는 인증+공개 Run 존재 확인+page3 SELECT이며 구간별 N+1이나 COUNT가 없다. 쿼리 구조만 재사용하고 데이터·권한·ORM 인스턴스는 캐시하지 않는다. 페이지 쿼리에도 소유권·활성 여부를 재적용한다. 조회 코드가 claim/heartbeat/expiry/복구/TaskEvent sequence를 변경하지 않는다. 기존 인증의 User FOR SHARE 보호는 유지하므로 전체 HTTP 요청을 PostgreSQL read-only transaction으로 변경한 것은 아니다. 진단 쿼리 자체는 read-only transaction에서도 실행되는 것을 검증한다.

새 DB migration·환경변수·의존성은 없다. 기존 Agent 처리 로직·Run 접수·SSE·워커·Executor 이벤트 처리를 변경하지 않는다. 사용자 지연/부하 전후 시험을 통한 성능 개선 주장은 없다.
