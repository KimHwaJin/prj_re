# 019 공개 Run 수명 통합

상태: 구현·격리 PostgreSQL·회귀·패키지 검증 완료 / 베이스 병합·운영 배포 미수행 · 2026-09-29 · feature/public-run-lifecycle

## 문제와 범위

기존 Run은 그래프 1회 호출을 의미해 HITL resume마다 공개 ID가 바뀌었다. Task가 전체 수명을 보관하지만 프론트는 두 API와 여러 ID를 조합해야 했다. 최초 요청부터 Executor 완료까지 하나의 공개 ID를 유지한다는 확정 계약이 아직 구현되지 않았다.

내부 agent_runs/Task/Worker 점유·checkpoint ID·Executor binding은 보존한다. agent_runs.public_run_id로 구간을 묶고 공개 조회를 최신 구간+Task의 일관된 SQL snapshot으로 만든다. 최초 생성, 전용 resume, 목록/조회/join/cancel/logs/기존 SSE를 연결한다. 과거 Run 주소는 읽기 별칭으로 지원한다.

재개에는 현재 승인 단계의 resume_token과 명령별 Idempotency-Key를 요구한다. 같은 키·같은 요청 재전송은 재접수하지 않으며 다른 payload/오래된 승인 응답은 409로 거절한다. Executor 대기는 waiting_executor로 노출하고 사용자 입력 및 외부 취소 확인 없는 세션 해제를 금지한다.

API 계약 변경: 생성 endpoint의 command 방식은 전용 resume로 이동한다. 내부 Task 엔드포인트는 제거하지 않고 deprecated로 표시하며 재개에는 같은 토큰을 요구한다. Tasks 전체 제거·CRUD 정리·외부 Executor 취소·운영 복구 API는 이번 범위가 아니다.

## 마이그레이션 계획

0021은 같은 세션의 유효한 Task root → checkpoint metadata root → 자기 ID 순서로 공개 ID를 채운다. 연결이 루트가 아닌 다른 구간/순환을 가리키면 추측해 합치지 않고 upgrade를 실패시킨다. 기존 ID/FK/checkpoint 데이터는 바꾸지 않는다. API·이벤트 Worker를 drain/중지하고 업그레이드 후 새 코드를 기동해야 한다. 기존 writer와 혼합 배포는 지원하지 않는다.

## 검증

격리된 로컬 PostgreSQL `identity_test` DB만 사용했다. 실제 LLM·Executor·외부 Redis에는 연결하지 않았다.

- 전체 회귀: **370 passed, 2 subtests passed, 0 skipped, 43 warnings, 106.25초**. 경고는 기존 checkpointer 없는 그래프의 durability 경고다. 첫 전체 실행의 2개 실패는 새 resume token/상태 계약으로 바뀐 두 테스트 기대값을 갱신해 해결했다.
- 최종 집중 검증: **18 passed, 23.76초**. 전체 회귀에 포함된 새 검증 15개와 이후 추가한 SSE 1개/과거 taskless 취소 2개를 포함한다. 전체 370과 18을 합산한 수치가 아니다. 마지막 호환 취소 분기 보완 후 관련 전체 집중 검증을 다시 실행했다.
- wheel 오프라인 검증: 소스 checkout import 없이 OpenAPI 34개 경로, 역할별 프롬프트 7개, Mock graph 6개 execution step 확인. 실제 모델 및 Executor 호출 없음.
- `git diff --check` 통과.

확인한 동작:

1. 여러 HITL을 지나도 공개 ID/Location은 같고 내부 실행 행/점유 ID는 별도로 보존된다. 목록은 논리 Run당 1개이며 pagination·기존 ID 조회 별칭을 확인했다.
2. 8개 동시 재전송이 같은 명령으로 수렴한다. 다른 키 경합은 1개만 접수하고, 같은 키의 다른 payload/오래된 토큰/다른 Run 토큰·사용자 접근은 거절한다. 취소·resume 경합 후 추가 실행이 남지 않는다.
3. 실제 PostgreSQL checkpointer와 LangGraph로 두 HITL → Executor 대기를 만든다. runtime/풀을 종료하고 새 객체로 재생성한 후 checkpoint에서 이어서 성공/실패/취소 결과를 투영한다. 중복 완료 투영은 Task event sequence를 증가시키지 않는다.
4. 기존 Run·Task·metadata·checkpoint 참조를 보존한 0020→0021 업그레이드와 다운그레이드. 잘못된 UUID/다른 세션 참조를 잘못 묶지 않고 모호한 연결은 transaction rollback한다.
5. SSE가 구간을 넘겨 Last-Event-ID 이후 이벤트를 재생하고 동일 공개 ID의 상태/로그를 전달한다. DB 풀 연결 1개에서도 같은 SSE를 HITL 동안 열어 둔 채 다른 HTTP resume를 처리하며 yield 시 연결 점유 0을 확인했다.
6. Executor 대기/복구 필요 상태의 사용자 resume·신규 입력·잘못된 로컬 취소를 거절한다. 다른 세션 실행은 기존 회귀로 유지한다.

재현: 전용 로컬 `identity_test` DB URL을 `DTEST_IDENTITY_TEST_DATABASE_URL`에 넣고 `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest src -q`를 실행한다. 집중 검증은 `src/app/test/test_public_run_postgres.py src/app/test/test_public_run_migration_postgres.py`다. fixture가 테스트 DB public schema를 초기화하므로 애플리케이션 DB를 지정하지 않는다.

[검증 요약 JSON](../reports/public-run-lifecycle-validation-2026-09-29.json)과 [프론트/API·배포 계약](../public-run-api.md)을 참고한다.

## 실제 변경과 영향

- `AgentRunModel.public_run_id` + 0021: 내부 구간 ID를 변경하지 않고 공개 묶음과 조회 인덱스를 추가한다.
- `PublicRunService`: 최신 실행 구간+Task의 한 SQL snapshot으로 공개 상태를 계산한다. 별도 중복 상태 머신/장기 DB transaction을 만들지 않는다.
- Runs 라우터: 최초 생성/전용 resume 분리, 전체 목록·조회·로그·기존 SSE·취소 연계. 공개 상태에서 waiting_input과 waiting_executor를 구별한다.
- `RunService`: 요청 fingerprint, 오래된 interrupt 재개 거절, public ID 전파. FAQ 등도 Task를 삭제하지 않고 종료 이력을 남겨 공통 상태·event sequence를 유지한다. 과거 Task 없는 FAQ 이력은 별도로 호환한다.
- Task resume는 동일한 공개 접수 서비스에 연결하며 전체 Tasks 공개 라우터는 deprecated로 표시했다. 목록의 내부 구간 진단 계약 등은 아직 보존한다.
- 현재 공유 HTTP 부하 시나리오를 새 header/재개 계약에 맞췄다. 과거 고정 commit 비교용 harness·내부 데모는 일괄 수정하지 않았다.

## 남은 제한과 후속

- 이번 검증의 재시작은 프로세스 내 runtime/체크포인터 pool을 종료하고 새로 생성한 것이다. OS 프로세스 강제 종료·Kubernetes 재배포·실제 일주일 대기는 검증하지 않았다. 이벤트 입력/완료 상태는 로컬 제어하며 실제 외부 Executor·Redis를 통과하지 않았다.
- 공개 Run 단위 성능 A/B 테스트는 하지 않았다. 기존 성능 수치에 이번 변경 효과를 합산하지 않는다.
- Executor 외부 취소는 별도 계약/구현이 필요하다. 현재 409로 거절하며 세션을 조기 해제하지 않는다. 종료 불명 작업의 관리자 복구 API도 후속이다.
- Tasks 전체 제거·Message CUD 정리·나머지 CRUD 정책은 후속이다. 동적 Agent 등록/모델 선택/project_memory 저장도 이번 범위에 추가하지 않았다.
- `attempt_count`는 최신 실행 구간 기준이다. 기존 내부 진단 Task/Checkpoint ID를 응답에서 즉시 제거하지 않았다.
- 마이그레이션은 writer 중지 후 실행한다. 구/신 버전 혼합 배포를 지원하는 online migration은 아니다. 실제 Gaia·폐쇄망 배포는 수행하지 않았다.
- 원래 작업 checkout과 기존 서비스는 수정하지 않았다. 이전 완료분 013~018은 `851786b`까지 베이스에 통합했다. 이번 019는 파생 브랜치에 커밋하며 원격 push·배포는 수행하지 않는다.
