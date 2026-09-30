# 036. Agent Workflow JSON 계약 초안

| 항목 | 내용 |
|---|---|
| 상태 | 설계 초안·오프라인 계약 검증 완료 / 런타임 미구현 |
| 시작일 / 완료일 | 2026-09-30 / 설계 초안 2026-09-30 |
| 브랜치 | feature/agentic-workflow-contract |
| 관련 commit | `f6db511` — 사용자 요청으로 feature/refactor-base에 fast-forward 병합 |
| 배포 상태 | 미배포 |

**문제와 영향**

기존 Workflow API·compiler는 이번에 합의한 결과 기반 판단, 조건부 실행, 사용자 계획 편집, 정의와 실제 실행값 분리와 직접 현업 JSON 등록을 모두 표현하지 않는다. 구두 설명만 있으면 현업·Agent·프론트가 서로 다른 계획 형식을 구현할 수 있다.

**확인된 범위**

현재 Skill/Tool 목록과 실제 함수 signature를 확인했다. 품질·통계·이상치 탐지 Tool은 존재하지만 목표 예제의 결측 대체·범용 분석·재사용 데이터 저장 Tool은 구현되어 있지 않다. 새로운 계약을 현재 코드에서 실행 가능하다고 표시하지 않는다.

**실제 작업 결과**

| 변경 영역 | 결과 |
|---|---|
| docs/design/agentic-workflow-contract | JSON Schema, 현재 자산 기반 예제, 설명용 전체 흐름 예제와 검증 카탈로그 작성 |
| 설계 문서 | 입력 연결·조건·판단·승인·코드 고정·산출물·Finalize·API/Gaia 경계와 미정 항목 정리 |
| scripts/design/validate_workflow_draft.py | 오프라인 구조·참조·의존성·인자·조건 보호 검증 도구 |
| validation.json | 정상 예제와 잘못된 계약에 대한 검증 결과 |

원본 feature/total_merge_v1의 사용자 변경과 기존 feature/refactor-base 실행 코드를 수정하지 않았다. 실행 코드, API, DB, Executor, 환경 설정이나 배포를 변경하지 않았다.

**검증 결과**

`validate_workflow_draft.py --self-check --repository-root <refactor-worktree>`를 실행했다. 현재 자산 예제와 설명용 전체 흐름 예제를 검증하고, 미등록 Tool, 잘못된 인자, 의존성 순환, 잘못된 입력·판단 참조, 조건부 출력 보호 누락, SINGLE의 결과 판단, 코드 주입 등을 거부하는지 확인했다. 상세 결과는 [validation.json](../design/agentic-workflow-contract/validation.json)에 남긴다.

LLM·실제 Tool·API·PostgreSQL·Executor·Gaia를 실행하지 않았다. JSON 계약 검증은 E2E 실행 성공이나 데이터 분석 정확성을 의미하지 않는다. 런타임 코드 변경이 없어 기존 전체 회귀·부하테스트를 반복하지 않았다.

**영향과 후속 작업**

구현 전 초안이다. 반복 및 복잡한 분기 합류의 계약, 실제 승인 payload와 compiler, 데이터 참조 resolver, 중간 데이터 등록 API 방식과 Gaia 템플릿의 반환 계약은 후속 사항이다. 기존 Executor의 MULTI Finalize와 커널 종료 정책을 유지하고 지연 Finalize·커널 재사용은 제외했다.

**완료 판단**

설계 예제·검증 도구 작성은 완료했다. 신규 Agent·Workflow API·SSE·Executor 연계 구현과 배포는 완료하지 않았다. [작성 가이드](../design/agentic-workflow-contract/README.md)를 기준으로 다음 계약 검토를 진행한다.

**베이스 통합**

2026-09-30 사용자 요청으로 `f6db511`을 `feature/refactor-base`에 fast-forward 병합했다. 충돌·런타임 코드 변경 없이 통합했고 파생 브랜치는 보존했다. 원격 push·배포는 수행하지 않았다.
