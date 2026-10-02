# 후속 분석 답변의 근거·수치 처리

최초 실행 보고서는 서버가 실제 Executor 관찰을 표로 출력한다. 후속 conversation도 수치를 모델이 복사하는 대신 **Step/출력 항목을 선택 → 검증 → 서버가 원래 값 삽입**으로 처리한다. 기존 Run 요청/최종 응답과 SSE는 유지한다. 아래 JSON은 모델 내부 응답이며 프론트 API 요청이 아니다.

모델용 근거 목록의 예시는 다음과 같다. ID는 이 source 분석의 현재 목록에서만 유효하다.

```json
{"fact_catalog":{"statistics":{"f_a":{"label":"statistics.max_val.mean","value":4.2}}}}
```

모델은 실제 값 대신 ID를 선택한다.

```json
{
  "kind": "answer",
  "message": "확인된 값은 아래 표에 있습니다. 원인은 추가 검증이 필요합니다.",
  "plans": [],
  "grounding": {
    "scope": "analysis",
    "source_run_id": "이전 분석의 정확한 Run ID",
    "evidence_steps": ["statistics"],
    "fact_ids": ["f_a"],
    "facts": []
  }
}
```

기존 `facts=[{step_id, path}]` 형식도 지원하지만 현재 Conversation은 짧은 `fact_ids`를 우선 사용한다. 두 형식을 혼합하거나 ID를 중복 선택할 수 없다. 기존 `path`는 이전 공개 observation.summary에서 시작한다. typed dict/list/tuple의 `items` wrapper를 생략하며, 문자열 키와 배열의 정수 index를 구분한다. 실제 키에 맞게 선택한다. 없는 키·음수 index·큰 객체 전체는 거절한다. null/boolean/문자열/숫자 또는 작은 scalar 배열을 선택할 수 있다. 서버는 반올림, 단위 변환, 비율 계산을 하지 않는다. 문자열은 Markdown/HTML 셀용으로 escape한다.

- 모델은 정성 해석문과 근거 선택 목록을 별도로 작성한다. 서버가 선택 항목/실제 값 표를 붙인다. 본문 placeholder와 직접 작성 숫자·수치 표는 거절하고 기존 PromptJsonMiddleware가 최대 세 응답 시도 안에서 정정한다. 수치 없이 설명만 할 때 facts는 비울 수 있다.
- source Run과 user/project/session owner가 일치해야 한다. 존재하는 성공·완전 관찰만 근거로 선택하며 생략/실패/불완전 Step은 숫자 인용에 사용하지 않는다. 잘린 배열에 남은 작은 항목은 정확히 표시하되 일부 출력이라는 한계를 같이 출력한다.
- 서버가 공개 답변을 만든 뒤 SSE message.completed, 대화 history, final_response.message에 같은 문자열을 쓴다. 내부 grounding 객체는 공개 응답에 남기지 않는다.
- 보고서에서 설명을 빼거나 특정 항목을 강조하는 요청도 이 방식으로 답한다. 새 계산은 기존처럼 승인 계획과 새 Execution이 필요하다. Markdown 답변은 Artifact 등록이나 파일 저장을 의미하지 않는다.
- 같은 세션에서 완료 분석 문맥이 존재하면 답변에 grounding을 선언한다. 무관한 FAQ는 scope=general이며 분석 ID/근거/facts는 비운다. 문맥이 없거나 비활성화되면 일반 답변을 유지한다. mock provider의 고정 테스트 응답은 이 모델 계약을 적용하지 않는다.

새 필수 환경변수는 없다. 기존 `AGENT_SESSION_ANALYSIS_MAX_CHARS`(기본 16000, 0 비활성화)를 적용한다. 최종 fact_ids 또는 facts 최대 128개, selector 깊이 20개, 한 항목 escape 후 800자, scalar 배열 16개, 렌더링한 후속 답변 24000자 상한을 사용한다. 이 값들은 출력을 제한하기 위한 코드 상수이며 근거 문맥 설정과 별개다.

완료 분석 문맥에는 승인된 유효 Step의 SUCCEEDED/FAILED/SKIPPED/NOT_EXECUTED 현황을 추가했다. 크기 때문에 제외된 현황 수를 `omitted_step_outcomes`에 기록한다. 실행 도중 제외된 플랜의 모든 과거 버전, 다른 세션의 데이터, 전체 원본, 모든 보고서 버전을 저장하는 기능은 아니다. 기존 schema_version=1 기록은 새 항목 없이 읽을 수 있다.

## 검증의 의미와 한계

검증이 보장하는 것은 **이 owner의 실제 성공 관찰에서 선택한 항목을 원래 값대로 출력한다는 것**이다. 모델이 잘못된 항목에 올바른 숫자를 붙이거나 의미를 잘못 설명할 수 있다. scope를 general로 잘못 분류하는 행위도 자연어 의미를 이해해 자동 판별하지 않는다. 숫자를 한글로 쓰거나 근거 문자열에 숫자 설명이 포함된 경우까지 의미 검증하는 정리 엔진은 아니다.

최초 보고서·후속 답변 prompt는 관찰과 해석/가설을 구분한다. 평균/중앙값/사분위수의 대칭성만으로 균일·정규 분포를 단정하거나 IQR 후보를 데이터 오류/원인으로 확정하지 않도록 한다. 서버는 해석 범위와 실패·출력 제한을 표시한다. **citation 통과를 통계적 정당성, 인과관계 또는 정답 보장으로 해석하지 않는다.** 실제 모델 응답의 시나리오 검토는 별도로 기록한다. VLM, 신규 계산, 데이터 등록, project_memory, Artifact 시점은 이번 범위가 아니다.

## 후속 답변과 계획 문맥의 분리 (049)

동일 Conversation create_agent의 첫 모델 요청에는 짧은 답변 지침·완료 분석 근거·Reply Schema를 주고 메타데이터 도구와 전체 Workflow Schema는 제외한다. 기존 결과 설명과 Markdown 보고서 수정은 이 단계에서 answer로 반환한다. 새 계산은 내부 `kind=planning`, 등록 `skill_ids`, `plans=[]`, `grounding=null`로 Skill을 선택한다. PlanningContractMiddleware가 표준 ToolNode에서 Skill을 읽고 그 다음 요청에 `planning_prompt.md`와 전체 계획 계약을 붙인다. 이후 최종 answer/plans를 반환한다. 별도 분류 LLM 호출이나 공개 API 필드 추가가 아니다.

phase는 이번 invocation의 실제 메타데이터 Tool 응답으로 판단하며 공유 Agent에 세션별 상태를 저장하지 않는다. 선택 단계도 실제 Skill 등록 검증을 거친다. 계획 제안은 기존처럼 사용자 승인 대상이며 메타데이터 조회 자체는 Executor 실행이 아니다. [049 측정](reports/conversation-performance-2026-10-02.md)에서 조회·토큰 비용은 줄었으나 설명 답변은 수치 본문 정정 때문에 지연이 줄지 않았다. 수치 검증은 그대로다.

## 짧은 근거 ID의 의미 (050)

SessionAnalysisMiddleware는 Conversation에서만 `evidence_view=compact_evidence_view`로 모델용 문맥을 만든다. `analysis.fact_catalog`는 Step별로 근거 ID를 묶고 각 항목의 `label`과 원래 `value`를 제공한다. 서버는 ID를 실제 Step/path로 연결해 기존 검증과 값 렌더링을 사용한다. ID를 화면·API·DB에 저장하는 계약이 아니다. 다른 분석에서 같은 ID가 나와도 source_run_id와 owner가 다르면 사용할 수 없다. 공유 Agent에 세션별 목록을 저장하지 않는다.

`fact_catalog_limited`는 크기·항목 수 한도나 선택 불가능한 큰 값 때문에 일부 항목이 목록에서 빠졌음을 뜻한다. 내부 목록 상한은 `MAX_CATALOG_FACTS=512`이며, 모델용 근거 문맥에 기존 `AGENT_SESSION_ANALYSIS_MAX_CHARS`를 적용한다. 각 관찰의 `summary_limited`는 원본 관찰 내부의 잘린 값을 나타낸다. 기존 실패·incomplete·summary_omitted·omitted 값은 유지한다. ID로 선택한 문맥에 목록 누락이 있으면 공개 답변에도 제한을 표시한다.

원본 summary와 보고서는 보존한다. 모델용 report의 `server_evidence_table_in_catalog=true`는 그 보고서 마지막 수치 표가 현재 관찰로 다시 만든 서버 표와 정확히 같아, 중복 표를 근거 목록으로 대체했음을 뜻한다. 해석문은 유지한다. 제목만 같거나 실제 값이 다르면 표를 제외하지 않는다. 같은 Step의 관찰이 여러 개면 최종 관찰을 ID 목록과 렌더링 모두에 사용한다.

[050 변경·검증](improvements/050-compact-answer-facts.md)과 [측정 보고서](reports/answer-efficiency-2026-10-02.md)를 참고한다. 본문 수치 검증, 일반 FAQ 숫자 허용, 공개 Markdown·SSE·history 일치, 신규 계산의 실행 승인 경계는 유지한다.
