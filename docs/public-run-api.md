# 공개 Run API 계약

038 구현 · 2026-09-30. 현재 새 분석 Runtime은 **답변 또는 실행 계획 승인 저장까지** 연결한다. 승인 후 `success`는 계획 단계의 성공이며 Executor 실행 성공이 아니다. `result.final_response.status=plan_approved`로 구분한다. 다음 작업에서 승인 뒤 Executor 그래프를 연결한다.

## 요청·재개

등록된 공개 사용자 ID를 `X-User-Id`로 보낸다. 새 입력과 HITL 응답 모두 같은 경로를 사용한다.

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

POST `.../runs/{id}/cancel`은 기존 취소 처리와 실행 종료 확인을 유지한다. Executor 대기 중 로컬 상태만 종료하는 취소는 여전히 거절한다. Task는 조회 진단만 제공한다.

## 파일 입력·배포 이행

입력 계약에는 `{"type":"image","file_id":"UUID"}`, `{"type":"file","file_id":"UUID"}` 참조 형식을 마련했지만 업로드·소유권 검증·text-only/VLM 처리가 미구현이므로 현재 422로 명확히 거절한다. 전달되지 않은 첨부를 무시하고 분석한 척하지 않는다.

038의 그래프 node/state 계약은 이전 Agent와 다르다. **이전 그래프의 pending/대기 Run 및 checkpoint를 새 Runtime으로 자동 이어 실행하지 않는다.** 새 Runtime 전환은 기존 실행을 정리하고 새 세션/테스트 DB에서 검증한 뒤 진행한다. 실제 Gaia 제공 router, Executor 이벤트 재개, pgvector 추천·Workflow CRUD, 프로젝트 메모리, 전체 UI는 후속 단계다. 과거 벤치마크는 해당 이전 commit을 재현하는 자료로 보존한다. 현재 공용 loadtest는 계획 승인 대기만 측정하며 submit 모드는 아직 거절한다.
