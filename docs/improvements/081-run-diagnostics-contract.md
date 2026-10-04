# 081 — Task 진단 조회를 공개 Run 아래로 통합

- 날짜: 2026-10-04
- 브랜치: feature/run-diagnostics-contract
- 기준: feature/run-diagnostic-logs / 05a79df (080)
- 상태: 구현·검증 완료, 베이스 미병합·미푸시·미배포

## 문제와 승인된 방향

사용자 실행 단위는 공개 Run인데 별도 Task 목록/상세와 `/tasks/{task_id}/runs`를 공개하고 있었다. 관리 화면이 Task ID를 별도로 알아야 하고 공개 Run과 내부 실행 구간의 이름도 혼동된다. 사용자는 Task를 Run의 내부 관리 기록으로 이해하고 Run 중심의 경로 통합을 승인했다.

Task 테이블·Worker·세션 잠금·checkpoint·TaskEvent 관리는 유지한다. 외부에서 작업을 찾는 진입점은 Run 목록이며 진단은 해당 공개 run_id 아래에서 수행한다. Message CUD·Workflow CRUD와 운영 복구 API의 후순위 결정은 유지한다.

## 최종 API와 응답

- 소유자 `GET /api/v1/sessions/{session_id}/runs/{run_id}/diagnostics`: RunDiagnosticsResource.
- 소유자 `GET /api/v1/sessions/{session_id}/runs/{run_id}/invocations`: Page[RunInvocationResource].
- 관리자: 위 경로의 `/api/v1/admin/sessions/...` 버전2개. admin 역할로 타 사용자·소프트 삭제된 자원을 읽고 일반 경로의 소유권은 우회하지 않는다.
- diagnostics envelope는 run_id/session_id/observed_at/task/session_work다. task는 최신 invocation에 연결된 내부 Task, session_work는 현재 세션 전체의 점유/차단 원인이다. 과거 Run이 성공했어도 다른 작업이 미완료일 수 있다. UI 입력 제어는 기존 Session availability를 사용한다.
- invocations는 최초 호출과 각 HITL resume 등 공개 Run 전체 이력이다. invocation_id는 구간별, run_id는 공개 전체 실행 ID이며 내부 task_id도 진단 연결값으로 제공한다. 구간별 status/시도/실패/시각과 페이지를 유지한다. 기존 public_run_id 필드명을 외부 run_id로 통일한다.
- 기본50·최대200개, 양방향 created_at 정렬과 UUID tie-break, cursor, 날짜[from,to) 필터를 유지한다. 실패 객체는 기존 진단 형식이고 레코드 수 상한이 바이트 상한은 아니다.

기존 일반/관리자 `/sessions/{session_id}/tasks`, `/tasks/{task_id}`, `/tasks/{task_id}/runs` 6개 GET은 제거하여404다. 별칭이나 redirect는 남기지 않는다. 공개 접수/통합 resume·Run 상세·SSE·cancel·로그는 기존 계약을 유지한다. 연결된 Run이 없는 orphan Task의 독립 관리자 탐색을 새로 추가하지 않는다.

## 구현 구조와 데이터 경계

- routes/run_diagnostics.py: 소유자/관리자 조회만 등록한다.
- schemas/common/run_diagnostics_schema.py: 공개 envelope, 내부 Task, 세션 전체 진단, 실행 점유, invocation DTO를 역할별로 정의하고 필드 설명을 붙였다.
- services/run_diagnostics.py: 공개 ID/별칭·세션·권한을 SQL에서 판정한다. latest invocation과 Task/현재 점유/차단 원인을 한 statement snapshot으로 읽고 쿼리 구조만 재사용한다. 데이터나 권한·writable ORM을 캐시하지 않는다.
- 페이지 조회의 존재 확인은 ID만 읽는다. 페이지에도 소유권·활성 여부를 재적용하고 COUNT/N+1이나 Run 결과·입력/command/metadata/interrupt를 조회하지 않는다.
- 요청 invocation과 canonical root의 같은 Session 소속을 검사한다. 최신 구간 선택과 전체 이력에도 Session 범위를 재적용한다. Task 본문은 같은 Session이며 root가 일치하거나 누락된 레거시 연결일 때만 읽는다.
- Task 없는 과거 Run은200/task:null과 공개 전체 invocation 이력을 반환한다. 과거 데이터에서 다른 Task로 이어졌으면 최신 Task만 진단하고 전체 이력은 task_id로 잘라내지 않는다. 새로운 DB1:1 제약을 추가하지 않는다.
- 기존 취소·복구·점유 보호와 heartbeat의 의미를 유지한다. 오래된 heartbeat를 실행 종료 증거로 보지 않고 조회에서 갱신/만료/복구를 수행하지 않는다. 점유/lease token은 반환하지 않는다.
- 대체된 routes/tasks.py, services/task_diagnostics.py, schemas/common/task_schema.py와 구 테스트 파일을 제거했다. 필요한 회귀를 test_run_diagnostics_postgres.py로 전환하고 기존 취소 보호 테스트 사용처도 새 경로로 바꿨다. 내부 task_model/task_service는 실행 책임이므로 유지한다.
- 주 문서는 docs/run-diagnostics-api.md로 전환하고 이전 docs/task-diagnostics-api.md를 제거했다. 과거022 기록은 현재 계약 링크를 갱신하되 과거 검증 내용/보고서 증거는 다시 쓰지 않았다.

## 검증

격리 PostgreSQL17 localhost60745/identity_test만 사용했다. 기존 서비스DB·Redis·Executor·LLM/SSO SDK를 호출하지 않았으며 인증은 기존 HTTP identity double이다.

- 기존 진단·Run cleanup·공개 Run: **66 passed**. 상태6종, 과거 작업과 다른 세션 차단 원인, 오래된 heartbeat, 해제/복구 점유, owner/admin401/403/404, User/Project/Session 소프트 삭제, 페이지1/4/200 양방향·동시각 tie-break·205건 분할·토큰 비노출·이전 경로 제거를 검증했다.
- 로그8·기존 조회 비용13: **21 passed**. 추가 경계 검증과 함께 실행한 첫 subset은25passed/1failed였다. 실패는 전체 HTTP DB를read-only로 강제한 시험에서 기존 인증 User FOR SHARE가 거절된 것이라 인증 보호를 바꾸지 않고 진단 쿼리 자체의 read-only 검증으로 시험 범위를 수정했다.
- 추가 공개 ID/레거시/읽기 전용/가시성 경계 및 실제 API→Worker→HITL2회 재개: **6 passed**. Task 없는 Run·별칭, 다른 Task로 이어진 전체 history, malformed cross-session alias/Task, 진단 쿼리 read-only 실행, 존재 확인 뒤 Session 숨김의 page 재검사, 공개 Run/Task 고정과3개의 내부 invocation을 확인했다.
- 최종 통과한 PG 검증은 중복 재실행을 제외하고 **93개**다(66+21+6). 진단은 인증+snapshot2 SELECT, invocation page는 인증+ID 확인+page3 SELECT 예산을 유지했다. 시간/처리량 전후 성능 측정은 아니다.
- 전체 src: **681 passed, 455 skipped, 74 warnings**. 외부 서비스 필요 opt-in PG93은 별도 검증이며 합산하지 않는다. 기존 no-checkpointer durability 경고는 유지했다.
- 클린 staging wheel/isolated Python 검증 통과: 현재34paths(기존Task6개 제거·Run진단4개 추가), 새로운경로·스키마·retired3개모듈부재,5개Agent역할/리소스/조립 및 checkout 미참조.
- scoped OpenAPI14paths/34models,2개 새 payload schema,2개 응답 JSON/JSONC와 모든 필드 설명. 주석 검증 **28files·6,545주석·6inline blocks**, JSON/JSONC 동등성과 검증 규칙 보존 통과. 주석 파일 안내 목록도 갱신했다.
- git diff --check 통과. 새 migration·환경변수·의존성 없음. 실제 서비스 배포나 기존 컨테이너를 수정하지 않았다. 테스트 종료 후 이번 임시 DB와 helper 스크립트만 제거한다.

## 클라이언트 이행

Task 목록 대신 공개 Run 목록에서 대상을 선택한다. 상세는 session_id/run_id의 diagnostics, 구간 목록은 invocations로 바꾸고 응답의 nested task/session_work 및 구간 run_id를 적용한다. Task ID만 저장한 기존 클라이언트는 이전 root_run_id 또는 공개 Run 응답으로 공개 ID를 확보해야 한다. 관리자 API도 같은 형태로 전환해야 한다. 외부 관리 화면의 실제 이행 확인, 관리자 orphan Task 탐색, 실패 payload 바이트 정책·운영 복구/보존 기능은 이번에 구현한 것으로 표시하지 않는다.
