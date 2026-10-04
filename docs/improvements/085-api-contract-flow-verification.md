# 085 API 통합 계약 검증

- 날짜: 2026-10-04
- 브랜치: feature/api-contract-flow-verification
- 기준: feature/remove-unused-infrastructure-apis / 2f3b0f6 (084)
- 상태: 검증·문서화 완료, 베이스 미병합·미푸시·미배포

## 목적과 범위

사용자가 동의한 로그인→프로젝트→세션→Run→계획 편집/HITL→실제 Executor→SSE→완료 후 추가 입력 시나리오를 현재 API 계약으로 검증했다. SSO SDK와 모델 판단은 외부 제약/시험 반복성을 위해 고정하고 나머지 서버 간 흐름은 실제 로컬 서비스로 실행한다. 운영 기능을 추가하거나 기존 정책을 바꾸지 않는다.

## 변경

scripts/diagnostics/verify_api_contract_flow.py를 추가했다. 실제 쿠키 인증·CSRF·소유권을 쓰며 전용 로컬 DB/namespace를 검사한다. Agent/Event Worker에 같은 CRUD DB 설정을 전달하고 실제 Executor 요청을 관찰한다. 기존 transport fixture를 재사용하여 create_agent/middleware 실행을 유지한다. 서버/HTTP client/시험 Redis 자원 종료와 private 결과 저장을 포함한다. 이전 진단 도구의 endpoint/DB 설정 이력을 새 도구의 현 계약으로 혼동하지 않도록 실행 문서를 정정했다.

[상세 보고서](../reports/api-contract-flow-2026-10-04/README.md)와 [결과 JSON](../reports/api-contract-flow-2026-10-04/result.json)을 저장하고, Run/Workflow 문서에 편집 가능한 인자와 사용자 제외/실행 skip 차이를 명시했다. production API·Agent·Worker·Tool·Executor 코드와 response schema는 바꾸지 않았다.

## 검증

- 실제 loopback FastAPI·PostgreSQL17·Redis·Agent/Event Worker·Executor/Jupyter에서14항목 통과. 두 Execution의 실제 함수 실행·MULTI finalize, 같은 세션 입력 잠금·다른 세션 독립 처리, 로그인 갱신 후 HITL 보존, stale token409, 동일 승인 중복 제출 방지, SSE 재접속을 확인했다.
- PG 회귀77 passed/1 skipped, 추가 planning/cookie POST-SSE4 passed. 처음 skip한 케이스는 추가 실행에서 통과했다. 전체src suite는084 이후 production 변화가 없어 반복하지 않았다.
- py_compile·git diff --check 통과. 성능 A/B·대규모 동시 사용자 시험은 실행하지 않았다. 기능 시험13.771초를 성능 지표로 사용하지 않는다.
- 사내 직원 검증 결과만 fixture, 모델 transport만 고정했다. 실제 모델 이해/보고서 품질·사내 SSO 왕복·실제 UI는 미검증이다. Dataset registry와 보고서 Artifact 미구현/미확정 경계를 유지한다.

## 발견 사항과 다음 방향

1. 계획에 선언되고 편집 정책이 허용한 인자만 수정할 수 있다. Tool 함수의 모든 기본 인자를 자동 표시하지 않아 statistics.columns 편집422를 확인했다. 사용자의 파라미터 조정 기대를 충족할 화면/생성 범위를 다음으로 검토한다.
2. 사용자 제외 도구는 승인 계획에 보존하며 최종 skipped_steps는 실행 중 조건/의존성 skip이다. 두 목록을 프론트에서 합산한 의미로 해석하면 안 된다.
3. report.ready는 Run Markdown 제공이며 저장 파일/노트북 셀 등록 완료가 아니다. artifact_registration=deferred를 유지한다.

일회성 시험 결과를 위한 별도 운영 API·모델 호출 최적화·장애 대응을 추가하지 않았다. 새 Executor 실행 이력은 근거로 남기고, 기존 서비스/원천 데이터·원본 checkout은 수정하지 않았다. 베이스 병합·push·배포는 이번 범위에 없다.
