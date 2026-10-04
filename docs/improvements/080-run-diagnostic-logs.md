# 080 — Run logs를 Agent 실행 진단 기록으로 정리

- 날짜: 2026-10-04
- 브랜치: feature/run-diagnostic-logs
- 기준: feature/run-read-contract / d3ef8e5 (079)
- 상태: 구현·검증 완료, 베이스 미병합·미푸시·미배포

## 문제와 결정

사용자는 logs라는 이름을 유지하고 의미에 맞추기로 결정했다. 기존 스키마는 프론트 진행 화면용이라고 설명했지만 프론트의 중간 메시지/HITL/완료/재접속은 이미 SSE와 상세 GET으로 처리한다. 기존 logs GET은 소유권 확인을 위해 결과·실패·interrupt를 포함한 Run 상세를 읽은 뒤 로그 전체를 제한 없는 배열로 반환했다. 장기 Run이나 반복 HITL의 로그가 쌓이면 불필요한 상세 본문과 전체 로그를 읽게 된다.

logs는 어느 Agent/node/event에서 어떤 기록을 남겼는지 조사하는 **구조화된 진단 기록**으로 확정했다. 일반 화면 진행 API, 서버 stdout, tracing span과는 용도가 다르다. 별도의 admin 전체 조회를 추가하지 않으며 현재 소유권 정책을 유지한다.

## 실제 변경

- GET /api/v1/sessions/{session_id}/runs/{run_id}/logs 이름/경로는 유지하고 응답을 Page[AgentRunLogResource]의 items/page로 변경했다.
- 공통 cursor 페이지를 적용했다. 기본50·최대200개, 양방향 created_at 정렬, 날짜 [from,to) 필터다. 동시각은 log_id로 정렬하며 총 개수는 계산하지 않는다. 후속 페이지는 같은 정렬·필터를 사용해야 한다.
- agent_name/node/event/kind의 선택적 정확 일치 필터를 추가했다. DB 필드에 맞춘100/100/100/50자 상한과 빈 문자열 금지를 Swagger에 표시한다.
- 읽기 책임을 services/run_log_query.py로 분리했다. 세션 소유권→canonical public ID만 조회→선택 페이지를 한 쿼리로 읽는다. result/failure/interrupt/input/command/metadata는 조회하지 않는다.
- 모든 HITL invocation 로그를 공개 run_id로 묶고 기존 invocation ID 별칭도 지원한다. 요청 invocation·canonical root·로그의 session_id를 확인해 잘못 연결된 레거시 데이터에서도 다른 세션 로그를 읽지 않는다.
- 저장과 AgentRunLogService의 중복 방지·Log/TaskEvent 원자 생성은 변경하지 않았다. SSE 재생은 기존 TaskEvent를 읽으며 logs GET을 호출하지 않는다.
- log_id는 개별 기록 UUID, event_key는 invocation 내부 중복 방지 키, created_at은 DB 저장 시각이다. 공개 Run 전체의 event_key 유일성이나 실제 실행의 인과 순서를 약속하지 않는다. payload는 기존 생산자가 저장한 종류별 객체이며 HITL/resume/SSE 본문으로 해석하지 않는다.
- Run 문서, 예제/JSONC 필드 주석, scoped OpenAPI10paths/26models, payload schema, wheel 계약 검사를 갱신했다. 이전 문서의 목록 items=PublicRunResource 설명도 PublicRunSummary로 바로잡았다.

## 검증 결과

- 전용 PostgreSQL17 임시 컨테이너의 localhost57988/identity_test만 사용: **52 passed**. 신규 로그 계약8건과 기존 공개 Run·read budget·Log/TaskEvent 원자 저장 검증이다.
- 공개 Run의2개 invocation에205개의 동일 시각 로그를 저장해 limit1/7/200과 오름/내림차순을 전부 순회했다. log_id 중복/누락 없이 일치하고 기본50개를 확인했다. 정확 필터, 날짜 경계, 잘못된 cursor/limit/sort/길이,401/404, 관리자 소유권 우회 금지, 다른 세션 root/alias 및 malformed cross-session mapping을 확인했다.
- HTTP SELECT는 인증·세션 소유권·공개 ID·로그 페이지의 **4회**다. 페이지 쿼리는9컬럼·최대limit+1행, canonical ID 쿼리는1컬럼이다. Run의100KB 결과·실패·interrupt를 저장한 상태에서도 해당 컬럼이 로그 SELECT에 포함되지 않는다. N+1이나 총 개수 COUNT를 추가하지 않았다. 이는 조회 작업량 검사이며 사용자 지연/처리량 전후 측정은 아니다.
- 전체 src: **681 passed, 449 skipped, 74 warnings**. PG52는 별도 opt-in 실행이므로 합산하지 않는다. 기존 checkpointer 없는 durability 경고는 그대로다.
- 클린 staging wheel의 isolated Python 검증 통과:36개 API path, logs Page schema·필터·기본50/상한200,5개 Agent 역할 및 리소스 조립, checkout 미참조.
- JSON/JSONC 주석 검증: **26files·5,572주석·6inline blocks**, 값 동등성/검증 규칙 보존 통과. git diff --check 통과.
- 테스트 인증은 기존 HTTP identity double이고 실제 SSO/LLM/Redis/Executor를 호출하지 않았다. 새 migration·환경변수·의존성·서비스 재배포는 없다. 원래 checkout의 사용자 변경과 기존 서비스 컨테이너는 변경하지 않았다. 이번 임시 테스트 DB/스크립트는 제거한다.

## 적용 시 확인과 후속

기존 로그 조회 클라이언트는 배열 대신 response.items를 읽고 page.next_cursor로 후속 페이지를 요청해야 한다. 프론트의 진행/HITL은 기존 SSE와 상세 GET을 사용한다. 레코드 상한은 payload 바이트 상한이 아니며 live cursor 조회는 고정 snapshot을 보장하지 않는다. 관리자 전체 진단, 로그 보존/정리, payload 크기/노출 정책은 별도 요구사항과 검증에 따라 결정한다. 이번 변경을 로그 저장 축소나 tracing 구현으로 해석하지 않는다.
