# 090 최종 승인 계획과 보고서·후속 답변 문맥 일치

날짜: 2026-10-05. 브랜치 `feature/approved-plan-report-context`, 089의 `9a52d3f`에서 분기. 베이스 미병합·미푸시·운영 미배포.

## 확인한 문제와 원인

089에서 사용자는 최초 `max_val + x`를 요청한 뒤 최종 승인 시 `max_val`로 바꿨다. Executor는 최종 파라미터에 맞게 `max_val`만 계산했다. 그런데 보고서는 `x`를 원래 분석 목표로 설명하고 미산출을 언급했다.

`execution.report`의 모델 입력은 원래 `document.goal`, 관찰, 판단값, skipped_steps, 실행 상태였다. 최종 input_values/argument binding과 사용자 제외 Step은 빠졌다. `capture_analysis`도 최초 goal과 관찰·보고서만 보존했으므로 후속 모델은 최초 요청과 그 해석문을 다시 받았다. **실행 파라미터 누락이 아니라 보고서/후속 답변 입력 문맥의 누락**이 확인된 원인이다.

## 변경한 내용

`runtime/analysis_scope.py`가 검증된 현재 승인 snapshot을 해석용 구조로 투영한다. 최초 목표는 `requested_goal`로 분리한다. 이미 승인한 목표 문장이나 source hash를 새로 고쳐 실행 계획을 바꾸지 않는다.

| 내부 필드 | 의미 |
|---|---|
| requested_goal | 최초 제안 Workflow의 목표 문장(document.goal). 최종 실행 범위를 대신하지 않는다 |
| execution_scope.plan_id / plan_revision | 최종 계획 식별자·승인 revision |
| parameters_scope | 현재 유효한 승인 계획의 파라미터. 모든 과거 재시도 파라미터 원장이 아님 |
| steps[].step_id / skill_id / tool_id | 현재 승인 범위의 Step·Skill·Tool |
| steps[].arguments | 최종 literal/input/decision 값 또는 실제 실행 참조 |
| steps[].status | 최신 관찰의 SUCCEEDED/FAILED, 실행 중 SKIPPED 또는 NOT_EXECUTED |
| excluded_steps | 원래 승인 snapshot에서 사용자 제외한 Step, EXCLUDED_BY_USER |
| execution_scope_omitted | 세션 문맥 예산 때문에 전체 범위가 생략됐는지 |

workflow_input 값은 최종 input_values, agent_decision 값은 실제 확정 판단을 사용한다. step_output은 Step ID·selector만 보존하며 전체 커널 객체를 지어내지 않는다. dataset binding은 공개 ID·제목, system_context는 키만 제공한다. Tool 소스·내부 dataset 경로·system_context 값은 투영하지 않는다. 자유 텍스트 파라미터가 자동으로 민감정보 제거된다는 뜻은 아니다.

현재 report 입력과 완료 session checkpoint에 같은 투영을 사용한다. 다음 요청의 SessionAnalysisMiddleware는 기존 owner 검사·예산 제한을 거쳐 전달한다. 그래프 재구성 후에도 같은 범위가 유지된다. 수리된 실행 snapshot은 현재 값을 사용하고 사용자 제외 목록은 원래 승인 snapshot에서 복원한다.

report/conversation prompt는 최초 요청보다 최종 범위·실제 관찰을 우선한다. 사용자 제외를 누락·실패·미충족 요구로 해석하지 않으며 조건부 스킵과 구분한다. 후속 설명·Markdown 재작성은 기존 answer 경로를 사용한다.

## 예산과 호환성

기존 AGENT_SESSION_ANALYSIS_MAX_CHARS(기본16000, 0 또는2048~64000)를 재사용한다. metadata와 실행 범위가 예산 절반 안에 들어갈 때 전체 보존하고, 안 들어가면 execution_scope=null/omitted=true로 표시한다. 배열·문자열을 잘라 다른 승인값처럼 전달하지 않는다. 나머지 예산은 기존 관찰/보고서에 사용한다. 생략된 범위를 최초 goal로 추측해 복원하지 않는다.

기존 schema_version=1의 goal은 읽을 때 requested_goal로 정규화한다. 과거에 저장하지 않은 승인 범위를 자동 생성하지 않는다. 소유권·최근 완료 분석 하나라는 수명은 유지한다. 새 DDL·환경변수·API path/header/body·SSE envelope·Executor 규격·실행 슬롯 변경, 별도 모델/DB 호출 추가는 없다. 모델 입력 길이는 조금 늘며 처리량 개선을 측정한 작업이 아니다.

## 검증

- Analysis Agent 전체 회귀345개 통과. 이 안에 집중 회귀65개와 신규4개가 포함된다.
- 진단 모드·최종 모델 호출 관측13개 통과. 관측 hook은 실제 SDK 반환값/인수를 변경하지 않는다.
- wheel에 신규 analysis_scope와 두 역할 prompt 포함 확인. 추가 패키지는 설치하지 않았다.
- 실제 모델·API/DB/checkpoint/Store/Worker/Redis·Executor/Jupyter 검증과 실패 보존은 [상세 보고서](../reports/approved-plan-report-context-2026-10-05/README.md)에 기록한다.

검산은 파라미터/상태/소유권/문맥 전달을 확인하며 자연어 해석의 업무 정답을 자동 증명하지 않는다. 원본 모델 응답·실행 코드·데이터 head·인증 원문은 private 진단 파일에만 보존한다.

## 인수인계와 남은 범위

[완료 분석 문맥 개발 가이드](../agentic-session-analysis-context.md)를 갱신했다. 최신 코드 콘솔은 `http://127.0.0.1:18103/test-console`, 별도 임시DB53604다. 실제 모델·실제 Executor, 직원 검증은 synthetic admin fixture다. 기존18102 및 그 세션/DB는 보존하므로 최신 코드로 자동 갱신된 환경이 아니다.

실제 검증에서 **계획 생성 모델이 workflow_input과 편집 가능한 Tool parameter_controls를 중복 선언하여 승인 이전 검증에 실패하는 사례**도 확인했다. 실행/보고서 단계의 이번 문맥 누락과 별개이며 성공한 결과만으로 계획 생성 안정성을 완료 처리하지 않는다. 상세 원문·시도별 결과와 후속 항목을 보존한다. 보고서 정성·인과 해석 품질, 사내SDK/Gaia/배포, Registry/Artifact/Workflow CRUD, 모델 호출 수·운영 보류는 남긴다.
