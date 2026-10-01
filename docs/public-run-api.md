# 공개 Run API 계약

039 구현 · 2026-09-30. 답변·계획 승인에 이어 실제 Executor 제출·결과 판단·리포트·Finalize를 연결한다. 실제 제출 활성화 시 승인만으로 success가 되지 않으며, 최종 Executor 이벤트와 리포트 저장 후 `result.final_response.status=analysis_completed/analysis_failed`로 끝난다. 제출 비활성화 시에는 기존 plan_approved까지다. [실행·결정 화면·설정 안내](agentic-executor-runtime.md)를 참고한다.

## 요청·재개

SSO 로그인 쿠키와 `X-CSRF-Token`을 보낸다. `X-User-Id` 단독 인증은 제거했다. 새 입력과 HITL 응답 모두 같은 경로를 사용한다. [인증·Swagger 테스트](sso-authentication.md)를 참고한다.

`POST /api/v1/sessions/{session_id}/runs`

```json
{"input":{"content":[{"type":"text","text":"데이터 품질을 분석해줘"}]},"main_model_name":"default"}
```

`main_model_name`은 선택 사항이며 시작 시 서버의 모델 catalog에서 고정한다. 동일 Run의 resume에서는 모델을 변경할 수 없다. 모델 alias는 실제 모델명이 아니라 등록된 이름이다. `input`과 `command`는 둘 중 하나만 보낸다. `input.messages`, 임의 metadata, 별도 `/runs/{run_id}/resume` 경로는 새 공개 계약에서 제거했다.

202 응답에는 안정된 공개 `id`, 상태와 Location이 있다. `Idempotency-Key`는 필수이며 최대 255자다. 네트워크 재전송에는 **동일 키·동일 body**, 다음 액션에는 새 키를 사용한다. 같은 키를 다른 명령에 사용하면 409다. 재전송은 접수 당시 snapshot이 아니라 해당 Run의 현재 상태를 반환한다.

계획 대기 GET 또는 SSE의 `interaction.opened/updated`에서 계획·버전·현재 `resume_token`을 얻는다.

```json
{
  "run_id":"공개 Run UUID",
  "resume_token":"현재 대기의 UUID",
  "command":{"resume":{
    "action":"approve_plan",
    "plan_id":"선택한 계획 UUID",
    "plan_revision":1,
    "input_values":{"dataset":"default-nce"},
    "step_changes":[],
    "execution_overrides":{}
  }}
}
```

`edit_plan`은 수정한 화면을 다시 열고 `approve_plan`은 최종 수정과 승인을 함께 적용한다. 계획 후보 하나를 선택한다. editable 입력만 수정하고 Tool 인자 수정은 `step_changes=[{"step_id":"outliers","parameter":"method","value":"iqr"}]`로 보낸다. 이전 단계 결과로 연결된 DataFrame 등은 편집할 수 없다.

`excluded_step_ids`는 보내면 전체 제외 목록을 교체하고, 생략하면 기존 제외를 유지한다. 의존성이나 판단 근거를 깨뜨리는 제외는 422다. 빈 문자열, 미입력, JSON null은 서로 다르며 입력 schema가 결정한다. 미확정 필수 입력은 화면에 비어 있고 승인 전 입력해야 한다. 수정 후 변경된 계획의 revision은 증가한다. 오래된 token/revision은 409, 올바르지 않은 schema·데이터 범위·단계 제외는 422다. **API에서 검증에 실패하면 resume token을 소비하거나 Worker를 접수하지 않는다.**

세션이 실행 중이거나 Executor를 기다리면 새 입력/사용자 resume를 거절한다. 다른 세션은 독립적으로 사용할 수 있다. 현재 단계에서 자연어로 전체 계획을 다시 생성하는 resume 액션은 아직 연결하지 않았다. 취소 후 새 요청으로 다시 제안받을 수 있다.

## 스트림

`POST /api/v1/sessions/{session_id}/runs/stream`은 위와 같은 body·헤더로 접수하고 SSE를 연결한다. 응답 `X-Run-Id`, `Location`으로 공개 Run을 확인한다. 연결 종료 후에도 Worker는 계속 실행한다. 동일 키·body로 POST 재연결하면 새 실행을 만들지 않는다.

기존 Run에 연결하거나 재접속하려면 `GET /api/v1/sessions/{session_id}/runs/{run_id}/stream`을 사용한다. 브라우저 EventSource에서 사용자 헤더를 넣기 어려우므로 fetch 기반 SSE 클라이언트를 사용한다. `Last-Event-ID`는 마지막 durable sequence(0 이상의 정수)이며 구간을 넘어 이어진다. PostgreSQL 이벤트가 원본이며 LISTEN/NOTIFY와 프로세스 공유 cache로 연결마다 빠르게 DB를 폴링하지 않는다. HTTP 응답 대기 중 DB 세션을 보유하지 않는다.

```json
{
  "schema_version":1,
  "type":"interaction.opened",
  "sequence":5,
  "session_id":"세션 UUID",
  "run_id":"공개 Run UUID",
  "occurred_at":"2026-09-30T00:00:00+00:00",
  "data":{
    "interaction_id":"승인 화면 UUID",
    "revision":1,
    "kind":"plan_review",
    "status":"open",
    "resume_token":"현재 대기 UUID",
    "summary":"계획과 입력값을 확인하고 승인해 주세요.",
    "payload":{"plans":[],"notices":[]}
  }
}
```

위 plans 빈 배열은 envelope 설명용이며 실제 plan_review는 한 개 이상의 후보를 갖는다.

| 이벤트 | 화면 사용 |
|---|---|
| message.completed | user/assistant 대화, channel=answer/commentary |
| activity.started/completed/updated | 진행 과정의 제목과 작업 ID |
| interaction.opened/updated | 우측 계획·파라미터 승인 화면 |
| interaction.resolved | 승인 화면 닫기, 승인 계획 표시 |
| run.updated | 큐·실행·최종 상태 변경 |
| run.snapshot | 현재 GET 상태와 cursor를 재동기화 |

Durable 이벤트에는 SSE `id`와 envelope sequence가 같다. `run.snapshot`은 현재 조회 결과로 durable 이벤트가 아니며 `sequence/id/occurred_at` 없이 `cursor`를 제공한다. Snapshot 자체의 cursor로 저장된 이벤트 처리 완료를 추정하지 않는다. `data`는 확장 가능한 객체이고 알 수 없는 이벤트는 sequence를 기록하고 무시할 수 있다. LLM이 생성하는 내부 JSON 토큰, Tool 소스 코드, 체크포인트 state는 스트림에 그대로 보내지 않는다. 기존 저장된 `task.*`, `agent.event`도 새 envelope의 요약으로 변환한다. 모델 자체의 구조화 JSON을 문자 단위로 보여주는 token stream은 이 Runtime에서 비활성화했다.

## 조회·취소·진단

GET `.../runs`, `.../runs/{id}`, `.../runs/{id}/logs`, `.../runs/{id}/join`은 유지한다. join은 즉시 조회 별칭이다. task_id/checkpoint_run_id는 호환 진단 필드이며 클라이언트가 재개 대상을 만들 때 사용하지 않는다. 공개 ID는 여러 승인 구간에서 유지하고 내부 invocation ID는 구간마다 달라진다.

결과 기반 파라미터 확인은 `interaction.opened`의 `kind=decision_review`로 구분하고 동일 POST에 `action=approve_decisions`를 보낸다. [결정 화면 형식](agentic-executor-runtime.md#사용자-결정-화면)을 따른다.

POST `.../runs/{id}/cancel`은 기존 취소 처리와 실행 종료 확인을 유지한다. Executor 대기 중 로컬 상태만 종료하는 취소는 여전히 거절한다. Task는 조회 진단만 제공한다.

## 파일 입력·배포 이행

입력 계약에는 `{"type":"image","file_id":"UUID"}`, `{"type":"file","file_id":"UUID"}` 참조 형식을 마련했지만 업로드·소유권 검증·text-only/VLM 처리가 미구현이므로 현재 422로 명확히 거절한다. 전달되지 않은 첨부를 무시하고 분석한 척하지 않는다.

038의 그래프 node/state 계약은 이전 Agent와 다르다. **이전 그래프의 pending/대기 Run 및 checkpoint를 새 Runtime으로 자동 이어 실행하지 않는다.** 새 Runtime 전환은 기존 실행을 정리하고 새 세션/테스트 DB에서 검증한 뒤 진행한다. Executor 이벤트 재개는 039에서 연결했다. 실제 Gaia 제공 router, pgvector 추천·Workflow CRUD, 프로젝트 메모리, 전체 UI는 후속 단계다. 과거 벤치마크는 해당 이전 commit을 재현하는 자료로 보존한다. 현재 공용 loadtest는 계획 승인 대기만 측정하며 submit 모드는 아직 거절한다.


## MULTI 수정 확인 화면

040의 `interaction.opened`에서 `kind=repair_review`를 받으면 기존 POST에 `approve_repair` 또는 `reject_repair`를 제출한다. 현재 run_id/resume_token, interaction_id/revision과 proposal_sha256을 그대로 보낸다. 승인 범위를 높여야 하는 화면에는 allow_policy_escalation=true라는 명시 동의가 필요하다. revision 불일치는 409, 다른 제안 hash/허용하지 않은 code 필드/승인 누락은 422이며 token을 소비하지 않는다. [수정 승인 규격과 설정](agentic-execution-repair.md#승인-화면과-api)을 따른다. 최종 `result.final_response.repair`에는 시도 수·종료 이유·코드 없는 변경 이력·수정 Operation outcome을 제공한다. Executor 대기 중 입력 잠금과 기존 GET/SSE 경로는 유지한다.


## 실행 전 재작성·추가 질문

041에서는 같은 POST `/api/v1/sessions/{session_id}/runs`에 자연어 피드백을 보낸다. 로그인 쿠키·X-CSRF-Token·Idempotency-Key, 현재 run_id/resume_token을 사용한다.

```json
{"run_id":"공개 Run UUID","resume_token":"현재 token UUID","command":{"resume":{"action":"replan","interaction_id":"현재 화면 UUID","revision":1,"feedback":"이 후보들 대신 다른 전처리 방법으로 분석해 주세요."}}}
```

`interaction.opened/updated`의 `kind=planning_question`은 같은 형식의 `action=answer_clarification`과 feedback 답변으로 재개한다. plan_review는 replan, 질문은 answer_clarification을 받는다. `interaction.resolved`의 resolution은 replanning/answered/auto_approved를 추가 지원한다. 이후 새로운 후보 ID와 증가한 화면 revision을 사용한다. 이전 후보 승인은 거절한다. 실행 중 동일 세션 입력 잠금은 유지한다.

계획 view의 execution_kind/workflow_eligible/approval_mode, 중앙 설정 및 token 미소비 오류 규칙은 [계획 재작성 계약](agentic-plan-revision.md)을 따른다. 직접 code 필드를 받지 않고 공개 SSE에도 소스를 넣지 않는다.
