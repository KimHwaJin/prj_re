# 084 — 미사용 Jupyter·Redis 관리 API 정리

- 날짜: 2026-10-04
- 브랜치: feature/remove-unused-infrastructure-apis
- 기준: feature/project-crud-contract / d391780 (083)
- 상태: 구현·검증 완료, 베이스 미병합·미푸시·미배포

## 문제와 승인된 방향

Jupyter registry의 목록/상세/health API가 현재 Agent·Executor 실행 경로에 연결되지 않은 채 남아 있었다. 실제 실행 환경은 session kernel_profile과 Executor runtime profile로 정하지만, 별도의 DB 서버 목록을 공개하면 실행 대상 관리 기능처럼 오해할 수 있다. Jupyter health는 ORM 조회 후 외부 probe를 기다리고 commit하여 그 요청 동안 DB 연결도 점유했다. Redis ping은 일반 로그인 사용자에게 host/port/db/version을 반환하고 요청마다 별도 client를 만들었다. 둘 다 사용자 분석 기능에 필요하지 않았다.

사용자는 위 API와 연결된 코드·설정을 제거하고 실제 Executor·SSO·Worker Redis 기능을 유지하는 작업을 승인했다. 새 운영 API를 만들지 않는다. Message CUD·Workflow CRUD와 운영 복구·모델 호출 수 최적화의 후순위를 유지한다.

## 변경

- 공개4개 operation 삭제: Jupyter 목록·상세 GET/health POST, Redis ping GET. prefix에 대응하는 전체4paths를 router/자동 OpenAPI에서 제거한다. 삭제 경로는404다.
- 전용7개 파일 삭제: router2개, service2개, schema2개, Jupyter ORM1개. models namespace의 export/import도 삭제하며 호환 alias·빈 shim은 남기지 않는다.
- 전용 JUPYTER_ALLOWED_HOSTS/JUPYTER_HEALTH_TIMEOUT_SECONDS/JUPYTER_TOKEN_ENCRYPTION_KEY/REDIS_PING_TIMEOUT_SECONDS와 실제 소비 코드가 없는 REDIS_HOST를 제거한다. 중앙 설정 loader는 YAML/env에서 발견하면 값·암호를 노출하지 않는 오류로 이행을 안내한다. REDIS_URL은 API·SSO·이벤트 정본으로 유지한다.
- Fernet 코드와 직접 cryptography 의존성을 pyproject/CRUD 설치 목록에서 제거한다. uv lock/export는 offline으로 갱신했다. langgraph-api의 전이 의존성으로 cryptography는 잠금/생성 설치 목록에 유지된다. 다른 패키지 버전은 바꾸지 않는다.
- 실제 Executor HTTP/Streams, SSO 로그인 pool, Agent Worker·이벤트 consumer·checkpointer/Store, kernel_profile은 변경하지 않는다. 기존 /service/live·ready·metrics를 유지하고 별도 대체 endpoint를 추가하지 않는다.
- 기존 jupyter_servers 데이터와 Alembic0008 이력을 유지한다. 삭제 ORM이 autogenerate의 DROP TABLE로 이어지지 않도록 env.py의 reflected/unmapped table 제외에 해당 테이블을 추가한다. 다른 테이블/새 대응 모델 비교를 막지 않는다. 새 revision/실제 DB 변경은 없다.
- 현재 package smoke에 삭제 module/settings/schema/path 부재 검사를 추가했다. 과거 CRUD review helper의 inventory 목록에서 이미 제거된 Task/Jupyter/Redis 모듈을 제외하고 현재 Run 진단을 포함한다. 이 helper의 2026-09-28 전체 probe는 현 SSO 계약으로 이행한 acceptance suite가 아니므로 실행하지 않았다. 저장된 당시 보고서에는 현행 계약 링크만 추가하고 원래 수치·증거를 재작성하지 않는다.

## 검증과 결과

- 기동·설정·SSO·패키지 경계·migration filter 관련 **134 passed**. 실제 FastAPI에서 제거 경로GET/POST404, 기존health/ready, schema 부재, 삭제 설정5개×config/env의 fail-fast·입력값 비노출을 확인했다.
- 실제 Alembic env의 include_object를 로드한 뒤 SQLite의 실제 reflection/autogenerate 비교를 수행했다. jupyter_servers·공식Store테이블은 삭제 제안에서 빠지고 무관한 테이블은 비교 대상으로 남는 것을 확인했다. PostgreSQL 물리 migration/downgrade나 기존 token 복원 시험은 아니다.
- 전체src **692 passed,477 skipped,74 warnings**. 083의681 대비 새 설정 이행10case·migration 보존1case가 추가됐다. 외부 DB/통합 환경의 opt-in 시험은 이 실행에서 제외되며 기존 no-checkpointer durability 경고를 유지한다.
- 별도 임시 Redis7 `dtest-infra-cleanup-redis-20261004`, loopback53679에서 기존 SSO/Streams 회귀 **3 passed**. 로그인 TTL/revoke·동일 Redis에 독립 namespace/Stream, BLOCK중 로그인 pool 진행, bounded login pool wait/timeout을 확인했다. flush 없이 시험 namespace만 정리하고 --rm container를 종료했다. 기존 Compose/외부Redis를 사용하지 않았다.
- clean staging wheel과 `python -I` smoke 통과. checkout import 없음, 전용7파일/5설정/schema/API 부재, 전체앱 **30paths**(기존34에서4삭제), 기존 Project/User/Session/Run/SSO계약,5개 Agent역할의 리소스·builder 조립을 확인했다. wheel은 /tmp에서 만들며 소스 tree build 산출물을 남기지 않았다.
- uv lock offline check와 git diff --check 통과. 현재 코드에 삭제 모듈 import가 남지 않음을 확인했다.
- scoped Agent OpenAPI는 처음부터 해당4paths를 포함하지 않아 **18paths/44models 그대로**다. JSON·JSONC/schema·기존 응답 예제를 불필요하게 재작성하지 않고 안내를 갱신했다. 전체앱 자동 OpenAPI30paths와 이 범위를 구분한다.

이번 변경은 미사용 API/설정 계약 정리이며 Worker 스케줄링·처리량 개선이 아니다. 성능 A/B, 실제 LLM·Executor·사내SDK, 외부 UI 이행·배포는 실행하지 않았다. 실제 DB 테이블은 보존한다. 원본 checkout/.env·기존 서비스/컨테이너는 변경하지 않았다.

## 문서와 후속

[제거 경로·설정·DB 보존](../infrastructure-api-cleanup.md), [배포](../deployment-configuration.md), [현재 계약 안내](../contracts/agent-api/README.md), [후속 목록](backlog.md)을 갱신했다.

다음은 SSO→프로젝트→세션→Run→HITL/resume→Executor 결과/SSE의 통합 계약 검증이다. 실제 물리 테이블 정리, Jupyter 관리 화면의 별도 요구, Message CUD·Workflow CRUD·운영 복구·모델 호출 수 최적화는 이번 완료 범위에 포함하지 않는다. 기존 SDK·Executor 데이터 registry 미구현 경계도 그대로 남는다.
