# 022 Task 진단 조회 유지·실행 명령 Runs 통일

상태: 구현·격리 PostgreSQL/전체 회귀/패키지 검증 완료, 베이스 미병합·미배포 · feature/task-diagnostics · 부모 43f950f

## 문제·범위

Task의 is_active는 pending/running만 의미해 대기 중 세션 입력 가능 여부와 혼동된다. 실제 graph 소유권은 session_executions에 있다. Task 목록은 cursor가 없고 실행 구간 목록은 무제한이며, Runs와 resume/cancel/SSE가 중복된다.

소유자용 Task GET 3종을 유지하고 목록은 Page로 통일한다. 작업 자체 미종료와 세션 전체 작업/실행 소유권을 분리한다. 진단과 CRUD 보호가 동일한 미종료 predicate를 사용한다. 내부 실행 구간 ID와 공개 Run ID를 별도 필드로 노출한다. Task resume/cancel/stream은 제거하고 Runs에서만 제공한다. 관리자 전용 /admin 경로에서 타 사용자 및 숨김 자원을 읽기 전용으로 조회한다. 복구/점유 변경 API는 이번 범위가 아니다. Message CUD는 사용자 결정으로 후순위다.

## 검증 계획

격리 PostgreSQL에서 Task 상태별 진단, 다른 작업이 남은 세션, 종료 불명 소유권/오래된 heartbeat, 사용자 및 관리자 권한/삭제 자원, cursor 경계/쿼리수/제한, token·불필요한 실행 payload 미노출, 제거된 API와 기존 Runs 재개/취소/SSE를 검증한다. predicate 공통화로 CRUD 경합 회귀도 실행한다.

021의 43f950f까지 베이스에 통합했으며 022는 독립 기능 브랜치에서 수행한다. 기존 checkout/서비스는 유지한다.

## 구현 결과

- 소유자용 GET 3개 유지, router의 deprecated 표시 제거. Task resume/cancel/stream 3개 제거. 내부 Task/Run/event 저장·Worker 상태 전이는 유지했다.
- Task 목록과 내부 구간 목록 모두 기본 50/최대 200의 cursor Page로 변경. 구간 ID는 invocation_id, 공개 ID는 public_run_id로 명확히 구분한다.
- TaskResource의 is_active를 is_unfinished 및 session_work로 대체한다. 미종료 Task/Run/LLM/실행 점유 조건을 CRUD 보호와 공유하며 별도 상태를 저장하지 않는다.
- Task 상세와 세션 실행 owner·차단 사유는 한 SQL snapshot에서 읽는다. Session 전체 상태를 보고하므로 과거 성공 Task를 조회해도 다른 현재 작업 때문에 입력 불가일 수 있다. 다른 세션의 작업은 영향을 주지 않는다.
- 관리자용 GET 3개는 /admin 아래에서 require_admin을 적용한다. 타 사용자 및 soft-delete된 자원을 조회할 수 있지만 쓰기·복원·점유 해제는 하지 않는다. 일반 경로 소유권은 확대하지 않는다.
- Owner token과 Task lock_token을 응답에서 제외하며, 읽을 때도 token 값 대신 점유 여부만 선택한다. 해제된 owner의 과거 프로세스 정보를 현재 owner로 표시하지 않는다. heartbeat 만료를 자동 탈취 근거로 쓰지 않는다.
- 제거된 Task SSE에서만 사용하던 미사용 이벤트 조회 helper와 TaskResume/TaskCancel 스키마를 삭제했다. 현재 SSE는 공개 Run 이벤트 조회를 사용한다.
- 기존 Task 명령 경유 회귀 테스트를 Runs 호출로 전환하고, 제거된 경로의 404/OpenAPI 제외를 별도로 검증한다. Message 라우트는 유지했다.

## 검증

- 기존 Run 실행/공개 lifecycle/CRUD 회귀: **101 passed, 63.01초**.
- 신규 Task 진단 집중 검증: **36 passed, 27.03초**.
- 전체 회귀: **495 passed, 2 subtests passed, 0 skipped, 43 warnings, 182.41초**. 기존 checkpointer 없는 그래프의 durability 경고다. 앞의 101/36개는 전체에 포함되므로 합산하지 않는다.
- wheel: 소스 checkout import 없이 OpenAPI 34 paths, 역할별 prompt 7개, mock graph 6 steps 통과. 추가한 관리자 조회와 Task 명령 제거/Run 명령 존치를 패키지에서도 확인했다.
- 6개 Task 상태/복구 시나리오, 과거 완료 Task + 9종 다른 차단 원인, API/Executor owner의 8일 지난 heartbeat·점유 해제·종료 불명, 다른 세션 격리, 관리자·소유자·익명 권한, User/Project/Session 숨김, 양방향 cursor/동일 timestamp/날짜 범위/limit 1·4·200을 검증했다.
- 내부 호출 구간 205개에서 200+5로 페이지가 분할되고 누락·중복이 없었다. 인증 포함 상세 SELECT 2회, Task/구간 목록 SELECT 3회로 페이지 크기와 무관했다. 이는 쿼리 왕복 수이며 DB 내부 EXISTS 평가 비용이나 TPS 측정이 아니다.
- 명령 삭제 뒤에도 Runs의 여러 HITL·취소·Executor 대기·SSE와 CRUD 접수 경합 회귀를 유지했다.

재현 명령: 기존 전용 로컬 identity_test URL을 DTEST_IDENTITY_TEST_DATABASE_URL에 지정하고 PYTHONPATH=src python -m pytest src/app/test/test_task_diagnostics_postgres.py -q. fixture는 전용 DB 스키마를 초기화하므로 서비스 DB를 지정하면 안 된다.

## 적용·제한

[현재 API 계약(081에서 Run 하위 경로로 전환)](../run-diagnostics-api.md)을 따른다. Task 목록 배열→Page, is_active 제거, 구간 id→invocation_id, Task 명령/SSE 제거는 **클라이언트 변경이 필요한 계약 변경**이다. 새 migration·인덱스·환경변수는 없다. 기존 부하 시나리오는 Runs를 사용한다. 내부 demo의 레거시 Task 호출은 전환하지 않았고 필수 운영 호환 범위가 아니다.

진단은 조회 순간의 보수적 입력 가능 상태이며 실제 접수를 예약하지 않는다. Task 내부 waiting_input은 Executor 대기를 포함할 수 있으므로 화면 표시/재개는 Run 상태를 기준으로 한다. 공개 Run 연결이 없는 Task도 내부 진단은 가능하지만 임의로 새 공개 ID를 만들지 않는다.

실제 Kubernetes/운영 프론트 배포, 대규모 진단 조회 부하, 관리자 복구/외부 Executor 취소는 이번에 수행하지 않았다. 실제 외부 LLM/Executor/Redis를 호출하지 않았다. 021의 43f950f까지 베이스에 병합했으며 022는 별도 브랜치에 커밋한다. 원격 push·베이스 병합·배포는 미수행이다. 전용 검증 DB는 정리했고 기존 checkout/서비스는 유지했다.
