# 037. 계획 승인 화면과 Executor 제출 계약

| 항목 | 내용 |
|---|---|
| 상태 | 개발용 계약 prototype·오프라인 및 소규모 Tool 검증 완료 / 서비스 미연결 |
| 시작일 / 완료일 | 2026-09-30 / 계약 초안 2026-09-30 |
| 브랜치 | feature/agentic-workflow-contract |
| 관련 commit | `f6db511` — 사용자 요청으로 feature/refactor-base에 fast-forward 병합 |
| 배포 상태 | 미배포 |

**문제와 영향**

Workflow 정의만으로 프론트의 편집 필드, 승인 요청, 실제 제출 코드가 같다는 것을 보장할 수 없다. 잘못된 단계 제외나 데이터 객체 편집, 오래된 승인 화면, 결과 판단 전의 제출과 후속 Step sequence 초기화가 실제 실행을 바꿀 수 있다.

**선택한 방법**

프론트에는 코드 없는 계획 View를 보내고 사용자 수정만 받는다. 서버가 schema·의존성·접근 범위·승인 버전을 확인한 뒤 고정된 Skill·Tool 소스로 Operation을 생성한다. 고정 파라미터의 편집 범위를 명시하기 위해 036의 draft Step에 parameter_controls를 추가했다. 이 변경은 새 draft에만 적용하며 기존 API와 compiler는 변경하지 않았다.

**실제 작업 결과**

| 영역 | 결과 |
|---|---|
| docs/design/plan-interaction-contract | opened·updated·resolved SSE, 수정·승인 body, 내부 승인·제출·Operation·Finalize 예제 |
| scripts/design/plan_contract_prototype.py | 개발용 typed 계약·Projection·승인 검증·코드 생성 및 probe |
| 036의 schema와 검증 도구 | 고정 인자 편집 제어 및 참조 편집 금지 검증 추가 |
| 문서 및 검증 기록 | 생성 예제의 용도, 실제 실행 범위와 미구현 부분 명시 |

**검증 결과**

계약 probe 결과는 [validation.json](../design/plan-interaction-contract/validation.json)에 기록했다. 실제 함수에서 docstring만 제거한 코드와 시스템 호출·관찰 코드를 임시 12행·2컬럼 Parquet 데이터로 실행했다. 첫 Operation 0·1·2, 다음 Operation 3, 이상치 index 11과 텍스트 관찰 4개를 확인했다. LLM, API, DB, 실제 Executor 또는 고객 데이터를 실행하지 않았다.

현재 Executor의 Pydantic 모델은 별도의 Python 3.12 환경에서 최초 제출·Operation 추가·Finalize 요청을 검증했다. 잘못된 요청을 포함한 상세 결과는 [executor-schema-validation.json](../design/plan-interaction-contract/executor-schema-validation.json)에 기록했다. 요청 body 검증은 API 접수·Executor 실행 성공을 의미하지 않는다.

036 Workflow 계약 probe를 다시 실행했다. JSON·Python 문법·문서 링크와 diff whitespace도 확인했다. 실행 코드 변경이 없으므로 전체 서비스 회귀·부하테스트는 반복하지 않았다.

**경계와 후속 작업**

원본 feature/total_merge_v1의 사용자 변경과 실제 src 실행 코드를 수정하지 않았다. production의 승인 트랜잭션·멱등성·다중 후보·실행 중 편집·일반 반복과 조건 평가·Graph 및 API 연결은 미구현이다. intermediate Artifact API 변경·Gaia 템플릿 반환 계약도 미정이다. 지연 Finalize·커널 재사용을 추가하지 않았다.

**완료 판단**

설계 계약과 개발용 검증은 완료했다. 서비스 구현·배포는 하지 않았다. 이후 사용자 요청으로 계약·prototype 커밋 `f6db511`을 베이스에 fast-forward 병합했다. [계약 가이드](../design/plan-interaction-contract/README.md)를 기반으로 실제 계획·HITL·실행 준비 경계를 연결한다.

**베이스 통합**

2026-09-30 사용자 요청으로 `f6db511`을 `feature/refactor-base`에 fast-forward 병합했다. 충돌·런타임 코드 변경 없이 통합했고 파생 브랜치는 보존했다. 원격 push·배포는 수행하지 않았다.
