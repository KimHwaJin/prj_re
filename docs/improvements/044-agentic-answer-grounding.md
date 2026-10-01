# 044. 후속 설명·보고서 재작성의 실제 근거와 수치

| 항목 | 내용 |
|---|---|
| 상태 | 값 근거 구현·전체 회귀·실제 연계 완료 / 정성·후속 성능 보완 필요 |
| 시작일 / 완료일 | 2026-10-01 / 2026-10-01 |
| 브랜치 | feature/agentic-answer-grounding |
| 기준 commit | 0bd7b2881838e29b132120200f57e8ae60eb29b5 — 043에서 분기 |
| 구현 commit | 검증 후 기록 |

## 문제와 방향

043은 실제 분석 결과를 후속 conversation에 전달했지만, 최초 실행 보고서와 달리 후속 답변의 숫자는 모델이 다시 작성했다. 실제 평균·중앙값·사분위수만으로 균일 분포라고 설명하는 과한 해석도 확인했다. 목적은 이전 실행을 다시 수행하지 않고 결과 설명·Markdown 편집에 실제 근거를 사용하도록 하는 것이다.

모델은 정성 해석문과 보여줄 출력 항목을 선택한다. 서버가 같은 owner의 최근 완료 분석에서 항목을 찾아 원래 값을 표로 출력한다. 근거 인용은 의미적 정확성/인과관계 증명을 대신하지 않는다.

## 구현

- conversation 내부 Reply에 grounding(scope/source_run_id/evidence_steps/facts)을 선언한다. public Run/SSE/요청 body는 변경하지 않는다.
- 사실은 정확한 Step ID + summary selector로 선택한다. 성공·완전 관찰, owner·source Run, 필드 존재·출력 크기를 검증한다. 계산/반올림/단위 변경을 하지 않는다.
- 분석 해석문의 직접 숫자·수치 표를 거절하고 기존 PromptJsonMiddleware에서 최대 세 응답 시도 안에서 정정한다. 초기 execution_report와 같은 정성문 검증 함수를 공유한다.
- 렌더링한 동일 문자열을 SSE/history/final_response.message에 보관한다. Step 인용과 해석 범위, 실패·생략을 표시한다.
- 이전 분석의 승인 유효 Step 현황을 별도 보관한다. SUCCEEDED/FAILED/SKIPPED/NOT_EXECUTED와 크기 때문에 생략한 현황 수를 구분한다.
- 평균·사분위수 대칭을 분포 증명으로 해석하거나 IQR 후보를 오류/원인으로 확정하지 않도록 prompt를 강화한다. 일반 FAQ는 analysis를 인용하지 않는 general 범위로 유지한다.

[개발 안내](../agentic-answer-grounding.md)에 내부 JSON, selector, 상한, 설정 및 의미 검증 한계를 기록했다. 새 필수 설정·DB migration·Executor 소스 변경은 없다.

## 실제 첫 시험과 최종 구조 선택

초기 실험은 본문 placeholder와 facts 목록을 동시에 편집하도록 했다. 실제 모델이 여러 항목을 선택한 뒤 목록만 줄이고 본문의 참조 번호는 그대로 유지했으며, 본문 숫자도 직접 작성했다. 세 정정 시도를 소진했고 Run은 기존 consumed-request 복구 경로로 넘어갔다. 실제 분석 자체는 성공했지만 후속 설명은 실패했다. 이 시험을 성공 수에 포함하지 않는다.

최종 구현은 이중 편집을 제거하고 **정성 해석문 + 항목 selector 목록 → 서버가 별도 실제 값 표 출력**으로 단순화했다. 정확한 owner/Step/필드/숫자 검증은 유지했다. 추가 시험에서 모델이 표에 넣을 통계 항목을 상한보다 많이 선택했다. 관찰의 실제 필드 수를 고려해 selector 상한은 128개로 정했고 최종 답변 크기 24000자 상한은 유지한다. 모델 선택의 의미를 임의로 수정하거나 없는 항목을 채워주지 않는다.

## 검증

전체 API·Agent **834개 통과**, 78 warnings, 2 subtests, 305.80초. 신규 33개를 포함한다. 관련 65개 시험과 최종 신규 33개 시험은 중복되며 전체 숫자에 합산하지 않는다. 경고는 기존 no-checkpointer 역할 graph의 durability 경고다.

신규 시험은 숫자 원문/표 거절·field selector·owner/Run/Step 격리·성공/실패/불완전·생략·실패 분석의 부분 성공·typed summary·엄격한 key/index·HTML escape·FAQ/비활성화·두 JSON 전략 정정·정정 소진·concurrent cached Agent·SSE/history/final 문자열 일치·Executor 재제출 없음·Step 현황·정상 통계 44개 항목·최종 답변 확장 상한을 확인한다. 새 계산 요청의 승인/새 Execution/data_load 경로는 기존 회귀로 확인했다.

wheel 소스 checkout 없는 import, 12개 역할 prompt/production builder, 새 grounding 모듈 포함·테스트 제외 검증을 통과했다. 기준/현재 OpenAPI 34개와 정렬·compact UTF-8 JSON hash는 동일하다(`2111f96cee35ef4e32ea4148d8bb8059d69e4bfb4fa7195603fac2ca489448eb`). 신규 환경변수·API·schema migration은 없다.

### 실제 Executor·LLM 시험

기존 Compose Executor/Jupyter, 격리 Agent CRUD/checkpoint PostgreSQL, 별도 Redis namespace, qwen38-27b-nvfp4와 Phoenix를 사용했다. **최초 계획만 quality-review fixture**이며 실제 Python 함수 실행·조건 판단·보고서·후속 대화는 실제 서비스/모델이다. 전체 자연어 최초 계획 품질을 검증한 시험은 아니다.

| 구간 | 결과 | 시간 |
|---|---|---:|
| fixture 계획 | 승인 화면 도달 | 0.371초 |
| 승인→terminal/보고서 | 2 Operations + Finalize, report ready | 27.553초 |
| 결과 설명 | 원래 값 38항목, answer, 새 Executor 없음 | 75.217초 |
| Markdown 재작성 | 원래 값 49항목, load Step 제외, 새 Executor 없음 | 125.503초 |

양쪽 후속 답변에서 같은 source Run/실제 field를 검증했다. 서버 표의 행/컬럼 수, IQR 후보 수/비율 등은 각각 10000/8, 109/0.0109로 그대로 출력됐고 history/final 문자열도 일치했다. 분석 실행 중 새 입력은 409, 종료 후 접수는 202, 실제 SSE에서 코드 노출 없음, Phoenix trace 17개와 임시 18144 포트 종료를 확인했다.

### 시간·의미 품질의 한계

이 단계는 **성능 향상이라고 보고하지 않는다**. 각 후속 답변은 모델 호출 4번(불필요한 read_skill 조회를 포함한 2번 + 답변 2번)을 수행했다. 첫 답변의 직접 숫자 복사를 검증에서 거절하고 한 번 정정했다. trace의 모델 호출 구간 합계는 설명 74.855초, 재작성 125.260초로 대부분의 시간을 차지했다. 입력 토큰 합계는 각각 46856/61248, 출력 합계는 3958/8872였다. 중간 model spans의 입력 이력에 있는 Tool call을 새 호출로 중복 집계하지 않았다.

043의 후속 설명 24.434초·재작성 24.226초보다 이번 관찰 시간이 길다. 생성 텍스트·call 수가 다른 실제 모델 사례이며 통제된 latency A/B가 아니다. 작은 응답 계약·불필요한 metadata 조회·재검증 횟수를 줄이는 후속 성능 작업이 필요하다. 이 비용을 DB/Worker가 느려졌다고 해석하지 않는다.

실제 최종 답변은 균일/정규 분포를 확정하지 않고 원인은 미확인임을 설명했으며, Artifact 저장·새 계산을 했다고 주장하지 않았다. 그러나 사분위 구간을 “대부분”으로 표현하거나 최빈 범주를 “압도적”이라고 표현하고 결측 비율만으로 데이터 품질이 양호하다고 평가하는 과장이 남았다. 최대값만으로 오른쪽 꼬리의 형태를 확정하는 해석도 추가 검토가 필요하다. **숫자 값/인용 검증 통과를 정성 해석의 전면 정답 보장으로 보고하지 않는다.**

[실제 연계 요약](../reports/grounded-followup-actual-executor-2026-10-01.json), [실패한 두 실제 시험](../reports/grounded-followup-first-validation-2026-10-01.json), [회귀·패키지 검증](../reports/grounded-followup-verification-2026-10-01.json)에 조건과 제한을 보존했다. 원시 prompt/trace/전체 출력·연결 인증 설정은 저장소에 넣지 않는다. 기존 Compose·Executor 소스·원래 checkout을 유지했고 임시 API는 종료했다. 베이스 병합·push·배포는 수행하지 않았다.

## 남는 한계

선택한 항목의 값은 그대로지만 모델이 잘못된 항목을 선택하거나 정성 해석을 잘못할 수 있다. general/analysis 분류 역시 자연어 의미를 기계적으로 입증하지 않는다. 통계적 의미와 인과 설명은 별도 품질 검토가 필요하다. 한글로 표현한 수량, 숫자가 포함된 문자열의 의미까지 자동 검증하지 않는다.

정정 시도 소진 시 검증되지 않은 답변을 공개하지 않는다. 기존 Run 복구/실패 처리의 UX 개선은 운영성 후속 범위다. 이번 단계는 Worker 재시도/복구 체계를 재설계하지 않는다. Executor Dataset API·project_memory·Workflow 검색·VLM·Artifact 등록 시점은 보류한다. 처리량 A/B·Kubernetes 성능 검증은 이번 시험이 아니다.
