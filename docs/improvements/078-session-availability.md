# 078 세션 공개 Run·대화 입력 가능 상태

- 날짜: 2026-10-04
- 브랜치: `feature/session-availability`
- 기반: `feature/session-settings-contract` / `4d3ddcd` (077)
- 상태: 구현·관련 PG166 / 최종 부분 재검사111 / 추가 경합2 / 실제 graph3 / 전체src681 / wheel 검증 완료. 베이스 미병합·미푸시·미배포.

## 문제와 사용자 승인

세션 응답에는 이름·설정만 있고 현재 어느 공개 Run에 연결해야 하는지, 새 입력/HITL 응답이 가능한지 없었다. 초기 화면·새로고침·로그인 복귀에서 현재 Run을 다시 찾아야 했다. 기존 새 Run 접수는 미종료 Task와 owner 복구를 주로 검사한 반면 삭제/진단은 미완료 명령·Task·Run·LLM·실행 점유를 모두 검사해 판정이 달랐다.

사용자는 active_run과 일반적인 availability를 승인했다. availability는 실행기 상태나 CRUD 권한이 아니라 대화 입력 능력이다. 현재 Run UUID와 공개 status를 별도로 제공하고 allowed_actions/reason으로 프론트 동작을 정한다. 새로운 Session 상태 머신·checkpoint 조회·상시 Session 폴링은 추가하지 않는다.

## 구현

- 생성·단건 조회·목록·이름 변경 응답에 active_run `{run_id,status}`와 availability `{status,allowed_actions,reason}`을 추가했다. id/settings 등 기존 필드를 유지하고 DELETE는 204다.
- available은 새 요청 send_message 또는 현재 HITL respond_to_interaction을 구분한다. queued/running·점유 후처리는 busy/processing, Executor 대기는 busy/waiting_external, 취소 처리 중은 busy/canceling, 복구 플래그/여러 활성 Run의 불일치는 blocked/recovery_required다.
- public_status에 공개 Run 상태 계산을 모아 상세 Run과 세션 요약이 같은 규칙을 쓴다. 세션은 내부 invocation/token/lease/process·전체 결과·HITL payload·명령 payload를 반환하지 않는다. 상세 대기 내용과 최종 출력은 기존 Run GET/SSE로 읽는다.
- 세션 메타데이터와 activity를 scalar Bundle/lateral 한 statement snapshot으로 읽는다. 페이지는 기존 created_at/UUID cursor를 유지하며 세션별 N+1 호출이 없다. 미종료 Task root/비정상 활성 호출부터 후보를 골라 완료 Task에 속한 모든 과거 Run의 최신 호출을 반복 조회하지 않는다. JSON interrupt 전체 대신 Executor wait 여부만 DB에서 읽는다.
- 실제 새 Run/사용자 resume도 동일한 describe/require_input 정책을 기존 admission 잠금 아래 다시 검사한다. idempotency replay가 먼저이므로 busy/blocked라도 이미 받은 동일 키/body는 그대로 재사용한다. 오래된 resume token·revision·입력 검증은 기존 규칙을 유지한다.
- Task 없는 이전 interrupt는 해당 공개 Run의 최신 invocation으로 교체되었으면 미종료로 세지 않는다. 이 조건은 삭제·Task 진단·입력 정책에 함께 적용했다. Task·owner 복구 플래그와 오래된 heartbeat를 임의로 해제하지 않는다.
- 새 세션/이름 변경 commit 후 동시 삭제가 완료된 경우, 이미 성공한 쓰기를 404로 바꾸지 않고 201/200 + blocked/resource_unavailable로 응답한다. 이후 GET/목록은 기존 삭제 숨김을 유지한다. 변경 응답에서 새 scalar snapshot을 쓰므로 사용하지 않는 이전 SessionService.read와 정상 non-expiring DB factory의 중복 full-row refresh를 제거했다.
- [세션 계약](../session-api.md), [Run 계약](../public-run-api.md), [CRUD 정책](../crud-lifecycle-policy.md), wheel OpenAPI/패키지 검증을 갱신했다.

## 검증

검사 전용 PostgreSQL17 localhost55553의 identity_test/agentic_runtime_test/agentic_checkpoint_test만 사용했다. 기존 서비스 DB/그룹에 접속하거나 기존 컨테이너를 변경하지 않았다. HTTP 인증은 기존 테스트 identity double이며 실제 사내 SSO 로그인 시험이 아니다. 그래프 검사는 mock 모델/Executor 비제출이며 Worker/checkpoint는 실제다.

- 관련 6개 PG 모듈: **166 passed**. 새 상태 26개 검사, 읽기 예산, CRUD 삭제/접수 경합, 세션 설정, Task 진단, 취소/복구/정상 HITL 라이프사이클을 함께 확인했다.
- 마지막 중복 refresh/기존 read 경로 정리 및 검사 5개 추가 후 신규 상태·설정·CRUD 모듈 재검사: **111 passed**. 완료 명령이 HITL을 막지 않음, Run 없는 미종료 Task, 과거 taskless interrupt 완료 후 삭제 등을 확인했다. 위 166과 중복이므로 합산하지 않는다.
- 추가한 결정적 commit→삭제 검사만 별도 실행: **2 passed, 31 deselected**. 생성 201/이름 변경200 유지와 resource_unavailable, 이후 GET404를 확인했다. 마지막 2개는 위111에 포함되지 않았다.
- 별도 실제 PlanningRuntime/Worker/checkpoint: **3 passed**. 편집→새 token→오래된 token 거절→승인→동일 공개 Run 완료·멱등·SSE, 커널 선택 유지, 답변/모델 거절을 유지했다.
- 최종 전체 `python -m pytest -q src`: **681 passed, 432 skipped, 74 warnings**. 격리 DB/외부 연동 설정을 요구하는 skip은 위 opt-in 검사를 대신하지 않는다. 기존 no-checkpointer durability warning은 유지됐다.
- SELECT budget: Session GET은 인증 포함 **2 statements / 2 반환행**을 유지했다. 세션 목록은 **인증 + 프로젝트 소유권 + 한 페이지 snapshot = 3 statements**로 limit1/7/200과 양방향 cursor를 확인했다. 20세션에서 중복/누락이 없고 큰 Run 결과/metadata/입력/Message를 조회하지 않는다. 실제 DB CPU·물리 I/O·응답 시간 개선은 이 검사로 입증하지 않는다.
- 깨끗한 staging의 wheel 빌드와 isolated Python 검증 통과: 신규 상태/스키마/공통 status 모듈 포함, SessionResource 필수 activity 필드, SessionActiveRun/SessionAvailability 필드 목록, 기존37 paths·역할5개 조립과 checkout 미참조를 확인했다.
- `git diff --check` 통과. 새 환경변수/의존성/DB 구조 migration은 없다.

초기 DB 검사에서는 JSONPATH를 VARCHAR로 바인딩해 SQL 함수 오류가 나서 명시적 JSONPATH cast로 수정했다. 이후 생성 commit 뒤 삭제되면 새 응답 조회가404가 되는 경합을 찾아 mutation 성공 응답 경계를 보완했고 결정적 재현 검사를 추가했다. 테스트의 HITL 명령 문자열도 현재 typed command 계약에 맞춰 수정했다. 초기 실패/중단 출력을 최종 통과 수와 섞지 않았다.

검사 완료 후 일회용 DB 컨테이너와 임시 접속 설정을 제거했다. 기존18개 컨테이너, 원래 checkout의 사용자 변경, .env는 유지했다.

## 적용 범위와 한계

078 자체의 DB migration/새 환경 설정은 없다. 기존 075 등의 migration 배포 절차는 별개다. 구/신 접수 writer 혼합·우회 SQL은 동일 정책 보장의 밖이므로 배포 시 함께 업데이트한다. 현재 feature 계열의 검증이며 원래 checkout/기존 실행 컨테이너에 적용한 결과가 아니다.

조회는 snapshot이며 입력 슬롯을 예약하지 않는다. waiting_input이 보이더라도 owner/명령이 정리되기 전에는 busy일 수 있고, terminal도 점유 해제 전에는 busy일 수 있다. POST 409 시 최신 Session/Run을 확인한다. active_run=null만 보고 입력을 열면 안 된다. 미종료 Task만 남아도 busy/blocked일 수 있다. 여러 활성 Run에서는 대표 Run만 표시해도 사용자 재개를 허용하지 않는다.

SSE의 기존 envelope·이벤트와 새 요청/resume 통합 POST 경로는 변경하지 않았다. 초기/복귀 시 세션의 public Run에 GET/SSE를 연결한다. busy여도 이름 수정은 가능하며 삭제는 HITL을 포함한 미종료 전체를 거절한다. 복구 자동화·Task 명령 정리·UI 구현·실제 부하/처리량 개선 측정은 이번 작업에 포함하지 않는다. SELECT 횟수와 반환 행 검사는 물리 I/O·DB CPU·전후 응답 시간의 측정이 아니다.
