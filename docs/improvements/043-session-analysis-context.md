# 043. 완료 분석의 후속 대화 문맥·결과 판단 검증

| 항목 | 내용 |
|---|---|
| 상태 | 구현·전체 회귀·실제 Executor/LLM·wheel 검증 완료 / 베이스 미병합 |
| 시작일 / 완료일 | 2026-10-01 / 2026-10-01 |
| 브랜치 | feature/agentic-session-analysis-context |
| 기준 commit | 71c40710fa34909fdc7b2de0b16faafeb0fff76d — 042에서 분기 |
| 구현 commit | 기록 commit에서 확정 |
| 배포 상태 | 임시 격리 API 종료. 기존 Compose·Executor 소스·사용자 checkout 유지. 베이스 병합·push·배포 미수행 |

## 문제·작업 방향

사용자는 Executor Dataset API 구현 요청을 후순위로 미루고, 현재 데이터·Tool로 결과 기반 다음 단계 판단과 후속 질문·리포트 흐름을 검증하도록 요청했다. 042의 동적 파일 등록·조회 구현은 착수하지 않는다.

현재 결과 기반 조건·파라미터·후속 Operation·Finalize는 구현되어 있었다. 발견한 빈틈은 **최종 보고서/관찰을 다음 conversation 모델에 전달하지 않는 것**이다. execution_report는 Run 최종 결과를 만들지만 history에는 저장하지 않았고, 다음 receive는 observations/final_response를 비웠다. 다음 대화는 이전 계획 메시지만으로 실제 결과를 설명해야 했다.

보완 전 새 후속 문맥 시험 두 건은 해당 AgentContext/기록이 없어 실패했다. 같은 데이터 분석을 완료한 뒤 graph를 재생성하고 후속 설명을 요청하는 경로로 확인했다.

## 구현

| 영역 | 변경 내용 |
|---|---|
| 세션 checkpoint | terminal 확인 후 최근 분석 하나의 last_analysis_context 저장 |
| 저장 근거 | 실제 Step 관찰·결정값·공개 데이터 ID·보고서 발췌. 실패는 실패로 표시 |
| 다음 Run | 현재 실행 상태는 초기화하고 같은 user/project/session의 이전 근거만 유지 |
| middleware | conversation/plan_revision의 호출마다 근거 HumanMessage 삽입. 시스템 권한으로 올리지 않음 |
| 격리 | 공유 Agent cache에 record 저장 금지. 요청 owner 일치 검사·deep copy |
| 크기 | AGENT_SESSION_ANALYSIS_MAX_CHARS, 기본 16000. 숫자를 잘라내지 않고 생략 표시 |
| 새 분석 | 새 승인·새 Execution, data_load부터 실행. 이전 커널 변수에 의존하지 않음 |
| API | 기존 통합 POST/GET stream/header/body 유지. private context 필드 공개하지 않음 |

[개발·설정 안내](../agentic-session-analysis-context.md)에 저장 범위, 조절 방법, 누락 표시, 역할별 적용을 설명했다. Project memory, 데이터 등록, 모든 과거 분석 검색·보고서 버전 관리는 구현하지 않았다.

## 실제 연계에서 추가로 확인·보완한 부분

최초 실제 시험은 품질 통계 Operation 성공 후 decision HITL에서 멈췄다. 모델은 inspect_outliers=true, outlier_method=iqr을 제안했으나 outlier_method의 evidence_steps에 statistics만 넣었다. 승인 definition의 필수 근거는 profile/statistics 두 Step이었다. trace에서 실제 모델 응답의 이 차이를 확인했다.

기존 node는 계약을 올바르게 거절했으나 모든 제안값을 비우고 사용자 확인으로 넘어갔다. 모델 문구에는 실행을 승인한다고 되어 있어 UX도 혼동을 주었다. 이제 execution_review의 create_agent middleware가 decision ID·필수 성공 Step·output_schema·불완전한 판단을 검사하고 정정 이유를 전달한다. **최대 두 응답 시도 안에서만 정정**하며, 계속 실패하면 HITL을 유지한다. 필수 근거 조건을 느슨하게 바꾸지 않았다.

검증 실패는 private execution_review_validation_error에 남긴다. 모델이 근거가 부족하다고 정상적으로 사용자 확인을 요청하는 것은 실패로 바꾸지 않는다. 네트워크/취소를 정상 확인으로 숨기지 않는다. 첫 실제 실패 시험과 추출한 최소 decision evidence도 별도 리포트에 보존한다.

## 검증 결과

전체 API·Agent 회귀 **801개 통과**, 74 warnings, 2 subtests, 305.46초. 신규 24개 및 042의 계약 50개가 포함되며 별도 시험 수와 중복 합산하지 않는다. warnings는 기존 no-checkpointer 역할 graph의 durability 경고다.

- 신규 시험 24개: graph 재생성 후 결과 전달, 세 owner 차원 격리, 새 승인/Execution 및 재로드, 크기·JSON escape·생략, 설정 우선순위/비활성화, concurrent cached Agent·두 JSON 전략·retry, 실패 보관, 필수 근거 누락/잘못된 값/ID·중복 정정, 재검증 소진 HITL.
- 관련 131개 회귀는 문맥 구현 시점에 통과했다. 이후 추가한 review 정정 시험까지 포함한 전체 801개도 통과했다. 숫자는 중복 합산하지 않는다.
- wheel을 소스 checkout import 없이 검증: OpenAPI 34개, 12개 역할 prompt/production builder, 새 middleware/runtime 모듈 포함, 테스트 패키지 제외.

실제 연계는 기존 Compose Executor/Jupyter, 격리 Agent CRUD/checkpoint PostgreSQL, 별도 Redis 소비 namespace, on-prem qwen38-27b-nvfp4 및 Phoenix를 사용했다. **최초 계획만 quality-review fixture**이며 결과 판단·리포트·후속 conversation은 실제 모델이다. 코드 분석 함수는 Executor에서 실행했다.

| 실제 시험 | 결과 | 시간 |
|---|---|---:|
| 최초 품질 분석 계획 | 명시적 fixture | 0.374초 |
| 승인→Executor 대기 | 실제 API 접수 | 0.228초 |
| 승인→terminal/리포트 | 2개 Operation, Finalize, SUCCEEDED, report ready | 39.197초 |
| 방금 결과 설명 | answer 완료, 기존 네 Step 근거 전달, 새 Execution 없음 | 24.434초 |
| Markdown 재작성 | answer 완료, 로드 설명 제외·통계/한계 부각, 새 Execution 없음 | 24.226초 |

전달 payload는 11003자, 네 Step 모두 보관했고 report excerpt도 생략되지 않았다. 관찰 자체의 head/배열은 기존 bounded summary이므로 원본 전체 데이터 전달을 의미하지 않는다. 원래 Execution ID와 source Run ID가 각 후속 모델 호출에 전달된 것을 middleware 경계에서 확인했다. Phoenix trace 11개를 확인했고 테스트 서버를 종료했다.

후속 설명의 전체 행/컬럼 10000×8, IQR 이상치 후보 109 및 비율 0.0109는 실제 관찰과 대조했다. 모든 수치·정성 해석·인과를 자동 검증한 것은 아니다. 특히 평균/중앙값/사분위수만으로 균일 분포를 단정하는 표현은 과한 해석일 수 있어 후속 의미 평가·인용 정책이 필요하다. 흐름 시험 통과를 일반 업무 정답 보장으로 해석하지 않는다.

[기계 판독 검증 기록](../reports/session-analysis-context-verification-2026-10-01.json)과 실제 연계 요약을 참조한다. 원시 trace·전체 입력 데이터·인증 설정은 저장소에 넣지 않는다.

## 남은 범위

Executor Dataset API 구현 이후 실제 동적 데이터 등록·세션 간 재사용을 연결한다. project_memory, Workflow 검색/승격, Gaia adapter, 첨부/VLM, 보고서 Artifact 등록 시점은 후속이다. 이번은 후속 근거 전달과 결과 판단 계약 개선이며 처리량·지연 A/B나 Kubernetes 배포 검증이 아니다.
