# 101 API 패키지와 폐기 DB 저장소 정리

- 날짜: 2026-10-06
- 출발: feature/refactor-base, 19fbb206f4df2e27a463ba45256377d24e5febf3
- 작업: feature/api-service-cleanup
- 요청: 미사용 파일·패키지·설정을 빠르게 정리하며 기존 DB 데이터가 있다는 이유로 폐기 구현을 남기지 않는다.

## 문제와 변경

기능별 구현이 services/core와 root Worker, 두 Worker 패키지로 흩어졌고, 모델/schema가 common 아래 중복 중첩되어 있었다. production 소스에는 API 테스트까지 포함되어 있었다. 현재 실행과 관계없는 직접 LLM 모델·ProjectMember·Jupyter registry·구 Workflow store 및 DB 객체가 남아 있었다. Event 명령은 실 사용 원장 외에 구 EW 원장에 매번 중복 저장·갱신했다.

현재 책임별 api/infrastructure/resources/runs/workers/workflows/web로 이동하고 구 경로·호환 wrapper를 삭제했다. API 테스트는 tests/api_service로 이동하여 wheel에서 제외했다. Phoenix는 공통 service_runtime으로 옮겼다. 각 파일의 이동은 [구조·추적 문서](../api-service-layout.md)에 정리했다.

미사용 LLM/Agent schema·legacy repository·WorkflowStore·singleton bridge·중복 설정을 삭제했다. 실제 모델 설정은 AgentSettings로 모으고 기존 서비스 기본값·별칭을 보존했다. API token stream 버퍼 정책은 계속 사용한다. 별도 Workflow DB/토글은 삭제하고 현재 Workflow CRUD/search는 공통 DB로 유지했다.

DB의 폐기 9개 테이블·view 1개·enum 2개·agent_runs 필드 10개를 실제 삭제하는 CRUD/Event revision을 추가했다. 구 pending 사용자 및 READY/FAILED 이벤트는 id·payload·namespace·실패 정보·순서 조건을 보존하며 현재 agent_commands로 이관한다. runtime의 EW mirror insert/update/readiness와 상시 backfill CLI는 제거했다. 현재 테이블/checkpoint/Store는 삭제하지 않는다.

과거 Alembic은 기존 DB를 현재 head로 올리는 경로라 보존하며, 과거 측정 보고서의 옛 테이블명도 당시 증거다. 현재 소스·패키지에는 호환 구현을 남기지 않는다. build/lib 잔재가 삭제 파일을 wheel에 다시 포함하는 현상을 확인하여 생성 캐시를 제거하고 clean build로 검증한다.

## 검증

- API 909개 고유 항목 검증. 한 번의 전체 실행 합계로 주장하지 않고, 아래 분할 실행과 보완 회귀로 모든 현재 수집 항목을 확인했다. 초기 9개 skip은 별도 Planning DB 설정을 제공한 15개 연계 실행에서 확인했다. 중복 실행 수는 합산하지 않는다.
- 검증 구간: 최초 API 구간 551 통과 → 동시성 이후 115 통과 → 소유권 이후 235 통과 → 마지막 YAML 모듈 14 통과. 변경한 autogenerate 소유권 필터·launcher 15개를 최종 재검증했다. 같은 구간의 반복 항목과 15개 Planning 연계 중복을 제외한 고유 API 항목은 909개다.
- Agent·설계 관련 432 통과. API와 Agent의 import 경계·middleware·계획·승인·실행·리포트 자산을 확인했다.
- 별도 전용 PostgreSQL17/pgvector0.8.6·Redis7에서 CRUD·SSO·Streams·공통 claim·취소/종료·checkpoint 재개를 확인했다. 외부 실제 LLM/Executor/사내 SSO SDK는 사용하지 않았다.
- 실제 retirement revision에 구 LLM/Jupyter/멤버십/Workflow·Outbox/Audit 데이터를 넣고 삭제를 확인했다. 사용자·프로젝트 소유권·메시지·Run 결과·현재 Workflow/Store를 보존하고, 다른 namespace의 READY/FAILED 명령을 ID·event payload·업무 실패 횟수·오류와 함께 현재 원장으로 이관했다. 뷰·enum·구 전달 필드도 제거된다.
- clean wheel의 checkout 차단 설치 검사: OpenAPI 32개 경로, create_agent 역할 5개, 계획 승인, CRUD/session 계약, prompt·Skill/Tool 자산·/demo HTML 포함. 구 API 패키지·미사용 계약·테스트는 wheel에 없다.
- 소스/테스트/스크립트/migration 369개 Python 파일 문법 검사와 git diff 공백 검사를 수행했다.

검증에서 현재 계약과 어긋난 예전 fixture를 함께 수정했다. 설정된 recursion_limit=100을 무시하던 기대, 실행 명령이 남은 채 schema downgrade, 목록 응답에서 resume_token 조회, HITL/실행 중 새 요청을 허용하던 fixture, 삭제한 backfill CLI 참조를 바로잡았다. 실제 세션 잠금은 완화하지 않았다. 이동한 테스트 harness는 소유권 모듈의 fault injection을 동적으로 참조하며 주입 대기에 timeout을 둔다.

런타임은 테스트 이동을 제외하고 약 1,250줄 감소했다. 기존 파일 79개를 실제 책임별 위치로 옮겼고, 이동 추적 84개 중 5개는 최종 삭제했다. 별도로 API 테스트를 src 밖으로 이동했다. 단순 코드 정리 결과를 처리량 향상 수치로 주장하지 않는다.

## 적용 범위

기존 사용자 checkout은 변경하지 않았다. 실제 기존 서비스 DB의 DDL 적용·컨테이너 재기동·배포는 수행하지 않는다. 별도로 만든 테스트 PostgreSQL에서 실제 migration과 활성 데이터 보존을 검증한다. 서비스 반영은 [선택한 YAML의 migration 절차](../database_migrations.md)를 따라야 한다. downgrade는 빈 구 스키마만 만들며 삭제 데이터는 되살리지 않는다.

feature 브랜치 구현이며 베이스 병합·원격 게시 여부는 Git 이력/후속 요청에 따른다.
