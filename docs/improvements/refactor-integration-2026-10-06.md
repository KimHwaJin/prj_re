# 리팩토링 베이스 통합 기록 — 2026-10-06

사용자 요청에 따라 현재 완료된 구현·검증 이력을 `feature/refactor-base`에 통합한다. 원격은 [KimHwaJin/prj_re](https://github.com/KimHwaJin/prj_re)의 `origin`이다.

## 통합 범위

- 통합 전 베이스: `353f7a8`. 구현·검증 통합 지점: `beca20593f254e496145bd606a6e424bee04cde5`.
- `feature/workflow-hnsw-quality`까지의 선행 55개 커밋을 fast-forward로 병합했다. 충돌 해결이나 서비스 코드의 추가 수정은 없다.
- 058 이후 현재 브랜치에 포함된 설정·Worker·조회·CRUD·테스트 화면·자산 독립 계획·Workflow 표준·추천 구현과 검증 이력을 함께 반영한다. 다른 브랜치에서 보류한 실험 후보를 새로 채택하지 않는다.
- 통합된 이력에 속하고 기존 베이스 이후인 파생 브랜치 35개를 보존한다. 베이스와 함께 force 없이 atomic push하고 원격 commit을 대조한다. 기존 사용자 checkout은 변경하지 않는다.

## 최신 변경과 검증 근거

[094](094-workflow-hnsw-retrieval.md)는 다중 `user_queries` 등록, 임베딩 게시 버전 검사, pgvector HNSW, Workflow별 후보 수집과 후보 내 재정렬, E2E 계획 추천을 구현했다. 당시 회귀 800건, 관련 PostgreSQL 27건, HTML 9건 및 migration·wheel 검증 기록을 보존한다. 이 수치는 이번 통합에서 재실행한 결과가 아니다.

[095](095-workflow-hnsw-quality.md)는 서비스 검색 소스와 기본값을 유지하며 재현·검산 도구와 진단 보고서를 추가했다. 순차 2,646측정·별도 432요청 burst, 추가 인덱스 구축 96회·조회 960회·경계 조사 12회 및 독립 검산 4,162건을 기록했다. 실제 PostgreSQL 회귀 14건을 통과했다. 합성 데이터 결과를 실제 자연어 추천 정확도로 해석하지 않는다.

095까지의 소스는 fast-forward로 그대로 통합했으므로 병합 때문에 전체 테스트를 반복하지 않는다. 문서 변경은 diff와 링크를 확인한다. 기존 보고서의 ‘미병합·미푸시’ 문구와 `validation.json`은 해당 검증 시점의 이력이며 이 문서가 이후 통합 상태를 설명한다. 검증 영수증을 소급 변경하지 않는다.

## 다음 구현 범위 — 아직 시작하지 않음

완전히 동일한 벡터의 검색 대표화를 먼저 구현한다. 같은 Workflow·모델 공간·검색 버전 안에서 동일한 float32 벡터만 대표화하고, 모든 등록 질문과 벡터 이력은 보존한다. 비슷한 벡터나 서로 다른 Workflow를 합치지 않는다. 검색용 투영과 원본 질문 이력·후보 내 재정렬의 책임을 구분한다.

등록·수정·삭제·승격·복제·재색인과 늦게 끝난 임베딩 작업에서 버전 검사 및 게시 원자성을 유지한다. 별도 Worker나 저장 서비스를 추가하지 않는다. 동일 벡터 500/1,000개 조건, 768차원 비동일 벡터 조건, 동시 변경과 원본 보존을 검증한다. 이 변경이 모든 HNSW 누락을 해결한다고 보장하지 않는다.

실제 임베딩 endpoint·모델·차원 및 자연어 평가 데이터는 이후 실제 품질 검증에 필요하다. 이번 통합에서는 서비스를 재기동·배포하거나 운영 DB migration을 적용하지 않는다.

## 102·103 서비스 구조 정리 통합

- 통합 전 베이스: `87dad64`. 작업 브랜치: `feature/dtest-service-structure`. 대상: `origin/feature/refactor-base` (KimHwaJin/prj_re).
- 단일 src/dtest 아래 API·Agent·Worker 및 application/contracts/infrastructure/settings 조립 경계를 정리했다. 구형 엔진·중복 catalog/schema·미등록 임시 자산·전용 테스트36개를 승인 후 삭제했다.
- Executor v1 API 경로9개를 연동 모듈에 통합하고 YAML/env의 개별 PATH 설정을 제거했다. root base와 프록시 prefix를 제출·조회·이벤트 이력 조회에 동일 적용한다.
- pyproject/lock의 production dependency 그룹과 사내 CICD requirements.txt를 일치시켰다. Tool 검증용 ML/plot 의존성은 개발 그룹에 있다.
- 구조 변경의 전체 API907항목 실행 및 실패 항목 수정 재검증 기록은102에 있다. 마지막 Executor 경로 변경 이후 관련 API174개·Agent377개와 독립 설치 wheel 검증이 통과했다. 이를 새 외부 Executor E2E/부하 측정 결과로 해석하지 않는다.
- 원본 사용자 checkout은 변경하지 않는다. 별도 작업 checkout에서 파생 브랜치 커밋과 베이스 merge 이력을 남기고 두 브랜치를 force 없이 atomic push한다.

세부 이동·삭제·설정 이행 방법은 [102](102-dtest-service-structure.md), [103](103-executor-api-route-contract.md)를 따른다.


## 110 코드 품질·불필요한 코드·구조 정리

`120c555`에서 `feature/code-quality-structure-cleanup`으로 작업했다.
HTTP 의존성 별칭, application Run 제출·선택적 메시지 세션 생성, 파일 무결성
공통 경계를 정리했다. 미사용 옛 manifest/호환 helper/응답 DTO와 과거 CRUD
검토 스크립트를 제거했다. 등록 Skill·Tool·Workflow 실행 내용은 유지한다.
운영 의존성에 있던 LangGraph CLI는 dev로 이동했고 lock 패키지는143→135다.

분리된 PG/Redis 전체 회귀1,340통과/2skip, 새 파일/경계 검증을 포함한
관련 검증54통과와 개발 패키지 없는 설치 wheel 스모크를 완료했다.
Ruff3,500→3,079, ty772→759이며 새 진단은 없다. 전체796파일 포맷은 통과하고
전체 lint/type 통과는 아직 아니다. [110 기록](110-code-quality-structure-cleanup.md).

Artifact 제출/Workflow 검색 계약 삭제는 자동 승인 검토의 외부 계약 위험
판정으로 유지했다. HNSW 추가 개선보다 코드 품질과 타입 경계 정리를 우선한다.
운영 재배포나 사용자 DB migration은 수행하지 않았다.
