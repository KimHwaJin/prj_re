# Agent 호출과 응답 API

프론트 개발자와 API 연계 개발자를 위한 현재 코드 기준 계약이다. 2026-10-02의 053까지 반영한 코드와 OpenAPI snapshot에서 확인했다. 실행 중인 과거 Docker 이미지의 API와 같다고 가정하지 않는다.

새 요청과 모든 HITL 재개는 같은 POST를 사용한다. 일반 POST는 실행을 durable queue에 접수하고 Run 상태를 반환한다. 진행 메시지·승인 화면·최종 결과는 상태 GET 또는 SSE로 받는다. 최종 LLM 답변을 일반 POST의 응답까지 기다리는 방식이 아니다.

[프로젝트 공유 메모리 API](project-memory.md), [SSO 설정](sso-authentication.md), [Workflow JSON](workflow-json-reference.md), [검증된 JSON 예제와 schema](contracts/agent-api/README.md)를 함께 참고한다. 이미지·파일 입력과 Gaia adapter, 새 Workflow 관리 API·pgvector 추천, 동적 Dataset Registry는 아직 연결되지 않았다.

본문 `jsonc` 예제와 [주석 파일 안내](contracts/field-comments.md)는 필드별 설명을 포함한다. API에 전송할 때는 주석 없는 `.json` 예제를 사용한다. 주석은 요청 필드가 아니다.

## API 목록

기본 prefix는 `/api/v1`이고 `API_V1_PREFIX` 설정으로 바꿀 수 있다. 아래 경로는 prefix 뒤에 붙인다. session_id/run_id는 UUID다. 먼저 소유한 프로젝트와 세션을 생성해야 한다.

| Method | 경로 | 용도 | 정상 응답 |
|---|---|---|---|
| POST | /sessions/{session_id}/runs | 새 요청 또는 HITL 재개 | 202 + PublicRunResource |
| POST | /sessions/{session_id}/runs/stream | 접수 후 SSE 연결 | 200 + text/event-stream |
| GET | /sessions/{session_id}/runs/{run_id} | 상태·대기 내용·최종 결과 | 200 + PublicRunResource |
| GET | /sessions/{session_id}/runs/{run_id}/stream | 기존 Run 구독·재접속 | 200 + text/event-stream |
| GET | /sessions/{session_id}/runs | Run 목록 | 200 + 페이지 JSON |
| GET | /sessions/{session_id}/runs/{run_id}/logs | 실행 로그 | 200 + 로그 배열 |
| GET | /sessions/{session_id}/runs/{run_id}/join | 즉시 상태 조회 별칭 | 200 + PublicRunResource |
| POST | /sessions/{session_id}/runs/{run_id}/cancel | 취소 요청 | 202 + PublicRunResource |

`join`은 완료까지 기다리는 API가 아니다. 별도 `/runs/{run_id}/resume`이나 공개 직접 `ainvoke` API는 없다. 현재 RunRequest에는 agent_id/workflow 선택 필드가 없으며 기본 분석 Runtime을 호출한다. 플랫폼의 `/api/v1/{workflow}/run`은 이번 레포의 별도 구현 완료 API가 아니다.

## 인증과 헤더

SSO 로그인 후 브라우저가 관리하는 HttpOnly 쿠키를 사용한다. 기본 cookie name은 dtest_session이며 설정으로 바꿀 수 있다. X-User-Id·Bearer 문자열·body.user_id는 로그인 쿠키를 대신하지 않는다.

| 헤더 또는 클라이언트 설정 | 적용 |
|---|---|
| 로그인 쿠키 / fetch credentials | 모든 Agent API |
| X-CSRF-Token | 모든 POST. GET /api/v1/users/me의 csrf_token 사용 |
| Content-Type: application/json | JSON body 전송 |
| Idempotency-Key | POST /runs와 /runs/stream 필수. 최대 255자 |
| Last-Event-ID | GET/POST stream에서 선택. 0 이상 정수, 생략 시 0 |
| Accept: text/event-stream | SSE 클라이언트의 응답 유형 표시 |

```http
POST /api/v1/sessions/{session_id}/runs
Content-Type: application/json
X-CSRF-Token: <users/me에서 받은 값>
Idempotency-Key: <이번 액션의 고유 키>
```

브라우저에서 Cookie 헤더를 직접 조립하지 않는다. 같은 origin은 credentials:'same-origin', 별도 프론트 origin은 credentials:'include'와 실제 쿠키/CORS 정책을 적용한다. SDK가 미연결인 서버는 실제 SSO 로그인을 할 수 없으며 로그인은 503이다.

동일 요청의 네트워크 재전송은 같은 key·body를 사용하고 다음 액션은 새 key를 사용한다. 키는 세션 안에서 검사한다. 같은 키에 다른 body는 409다. 재전송은 최초 접수 snapshot 대신 Run의 현재 상태를 반환한다. 취소 POST에는 현재 Idempotency-Key 요구가 없다.

## 새 요청

```jsonc
{
  // 새 요청의 입력 객체. command와 함께 보내지 않는다. 현재 실제 입력은 text만 지원한다.
  "input": {
    // 입력 또는 Agent 메시지의 콘텐츠 블록 배열. 블록의 type으로 형식을 구분한다.
    "content": [
      {
        // 콘텐츠 블록 형식. text는 문자열 본문, image/file은 향후 첨부 참조이며 현재 text만 실제 접수된다.
        "type": "text",
        // 사용자 입력 또는 Agent 메시지 본문 문자열.
        "text": "이 데이터의 품질과 이상치 후보를 분석하고 보고서를 작성해줘."
      }
    ]
  },
  // 등록된 모델 별칭. 새 요청에서 생략하면 기본 모델을 사용하며 재개에서는 바꿀 수 없다.
  "main_model_name": "default"
}
```

| 필드 | 형식과 제약 |
|---|---|
| input.content | 1~20개 content 항목 |
| text 항목 | type=text, text는 1~12000자. 공백뿐인 전체 입력 거절 |
| main_model_name | 선택. 등록된 모델 alias, 1~128자, 영문·숫자·밑줄·점·하이픈 |
| command | 새 요청에서는 생략 |
| run_id / resume_token | 새 요청에서는 생략 |

여러 text는 줄바꿈으로 합친다. 모델 이름 생략 시 서버 기본 모델을 고정한다. 예제의 default는 모델 alias 예시이므로 실제 catalog를 확인한다. 같은 Run 재개에서는 모델을 바꿀 수 없다. 임의 metadata·input.messages·Python code는 새 공개 요청 필드가 아니며 추가 필드는 422다.

입력 schema에는 image/file 항목의 file_id UUID 참조도 있으나 현재 서버는 422로 거절한다. 업로드·소유권 검증·모델 전달이 미구현인 상태에서 첨부를 무시하고 분석하지 않는다.

[새 요청 전체 예제](contracts/agent-api/requests/start.json) · [필드 주석](contracts/agent-api/requests/start.jsonc).

## HITL 재개

같은 POST에 input 대신 command를 보낸다. input/command는 정확히 하나여야 한다. run_id와 resume_token이 필요하고 main_model_name은 재개에서 허용하지 않는다.

```jsonc
{
  // 사용자 요청 전체 흐름의 공개 Run UUID. resume에서도 같은 Run을 이어간다.
  "run_id": "11111111-1111-4111-8111-111111111111",
  // 현재 사용자 재개 대상을 확인하는 UUID 토큰. 서버가 내려준 최신 값을 사용한다. 로그인 인증 토큰이 아니다.
  "resume_token": "33333333-3333-4333-8333-333333333333",
  // 기존 Run의 HITL 응답 명령 객체. 새 입력 input과 함께 보내지 않는다.
  "command": {
    // 현재 대기에 맞는 action과 필드를 담은 재개 명령. 아무 액션이나 모든 화면에 보낼 수 없다.
    "resume": {
      // 재개 행위 종류. edit_plan/approve_plan/replan/answer_clarification/approve_decisions/approve_repair/reject_repair 중 현재 화면에 맞는 값을 쓴다.
      "action": "approve_plan",
      // 서버가 생성한 계획 후보 ID. 사용자 선택·편집·승인 시 현재 화면의 값을 그대로 보낸다.
      "plan_id": "55555555-5555-4555-8555-555555555555",
      // 계획 편집 버전. 현재 화면 값과 다르면 stale 요청으로 거절된다.
      "plan_revision": 1,
      // 계획의 입력 이름별 최종값. 전달한 키만 수정하고 기존의 다른 입력값은 유지한다.
      "input_values": {
        // Workflow 입력 이름 dataset. inputs는 정의, input_values는 해당 입력의 최종값이다.
        "dataset": "default-nce"
      },
      // Tool 함수 인자 편집 목록. step_id·parameter·value로 수정 대상을 지정한다.
      "step_changes": [],
      // 사용자가 제외한 Step ID 목록. 필드를 보내면 전체 제외 목록을 교체하고 생략하면 기존 목록을 유지한다.
      "excluded_step_ids": [],
      // 사용자가 조정하는 실행 정책. 허용 mode·수정 수준·횟수 상한 내에서만 변경된다.
      "execution_overrides": {
      }
    }
  }
}
```

위 ID는 설명용이다. 공개 run_id는 전체 작업에서 유지하고 resume_token은 현재 대기마다 바뀔 수 있다. GET 상태 또는 interaction 이벤트에서 받은 최신 값을 사용한다. 토큰은 SSO 로그인 토큰이 아니다.

| action | 화면 kind | resume 객체 필드 | 전체 예제 |
|---|---|---|---|
| edit_plan | plan_review | plan_id, plan_revision, 선택적 편집 필드 | [요청](contracts/agent-api/requests/edit_plan.json) · [필드 주석](contracts/agent-api/requests/edit_plan.jsonc) |
| approve_plan | plan_review | edit_plan과 동일, 수정과 승인을 함께 적용 | [요청](contracts/agent-api/requests/approve_plan.json) · [필드 주석](contracts/agent-api/requests/approve_plan.jsonc) |
| replan | plan_review | interaction_id, revision, feedback | [요청](contracts/agent-api/requests/replan.json) · [필드 주석](contracts/agent-api/requests/replan.jsonc) |
| answer_clarification | planning_question | interaction_id, revision, feedback | [요청](contracts/agent-api/requests/answer_clarification.json) · [필드 주석](contracts/agent-api/requests/answer_clarification.jsonc) |
| approve_decisions | decision_review | interaction_id, revision, values | [요청](contracts/agent-api/requests/approve_decisions.json) · [필드 주석](contracts/agent-api/requests/approve_decisions.jsonc) |
| approve_repair | repair_review | interaction_id, revision, proposal_sha256, allow_policy_escalation | [요청](contracts/agent-api/requests/approve_repair.json) · [필드 주석](contracts/agent-api/requests/approve_repair.jsonc) |
| reject_repair | repair_review | interaction_id, revision, proposal_sha256, 선택적 allow_policy_escalation | [요청](contracts/agent-api/requests/reject_repair.json) · [필드 주석](contracts/agent-api/requests/reject_repair.jsonc) |

### 계획 편집과 승인

plan_revision은 1 이상 정수이며 선택한 후보의 버전이다. interaction의 revision과 구분한다.

| 선택 필드 | 의미 |
|---|---|
| input_values | 입력명→값. editable 입력만 변경. 누락값·빈 문자열·null은 schema에 따라 다름 |
| step_changes | step_id, parameter, value 객체 배열. 편집 가능한 Tool 파라미터만 변경 |
| excluded_step_ids | 전체 제외 목록. 생략하면 기존 유지, 빈 배열이면 제외 해제 |
| execution_overrides.mode | SINGLE 또는 MULTI, 화면 allowed_modes 및 실행 가능성 검사 |
| execution_overrides.repair_level | 0~4 및 화면 repair_level_limit 이내 |
| execution_overrides.max_repair_attempts | 0 이상 및 화면 max_repair_attempts_limit 이내 |

input_values/step_changes/execution_overrides 생략은 기존 값 유지다. 이전 단계 객체·시스템 문맥 참조는 Tool 파라미터로 편집하지 않는다. 제외가 의존성·근거·필수 출력을 깨뜨리면 422다. 단순 편집은 LLM 재호출 없이 검증한다. edit_plan은 화면을 다시 열고 approve_plan은 승인 snapshot을 고정한다. 편집으로 실제 값이 바뀌면 plan_revision이 증가한다.

### 자연어 재작성과 추가 질문

feedback은 공백이 아닌 1~4000자다. replan은 계획 화면에서만, answer_clarification은 질문 화면에서만 받는다. 재작성에는 별도 횟수 제한이 있고 화면의 revision_policy에서 used/limit/free_code_allowed/free_code_require_approval을 확인한다. 이전 화면과 후보가 폐기되면 새 interaction revision·plan_id·resume_token을 사용한다. [재작성 상세](agentic-plan-revision.md).

### 실행 결과 판단과 수정 승인

approve_decisions의 values는 현재 payload.decisions의 decision_id를 모두 정확히 포함해야 한다. 각 값은 value_schema를 만족해야 한다. Agent가 제안한 값도 사용자가 확인·조정할 수 있다.

수정 승인은 화면의 64자리 소문자 proposal_sha256을 그대로 보낸다. requires_policy_escalation=true이면 approve_repair에 allow_policy_escalation=true라는 명시 동의가 필요하다. 서비스 ceiling을 넘을 수는 없다. reject_repair는 수정하지 않고 Executor 취소·종료 확인 경로로 진행한다. 이는 일반 공개 cancel의 waiting_executor 거절과 별개의 Agent 수정 판단 경로다. [수정 정책](agentic-execution-repair.md).

오래된 화면/token/revision은 409, 잘못된 action·schema·hash·승인은 422다. API에서 검증에 실패하면 token을 소비하거나 Worker를 접수하지 않는다.

## Run JSON 응답

[전체 접수 응답 예제](contracts/agent-api/responses/pending.json) · [필드 주석](contracts/agent-api/responses/pending.jsonc), [계획 대기 응답 예제](contracts/agent-api/responses/waiting_input.json) · [필드 주석](contracts/agent-api/responses/waiting_input.jsonc). 아래 표는 PublicRunResource의 모든 필드다. null 가능 여부·타입은 [schema](contracts/agent-api/payload-schemas.json) · [필드 주석](contracts/agent-api/payload-schemas.jsonc)에서 확인한다.

| 필드 | 의미 |
|---|---|
| run_id / session_id | 안정된 공개 Run UUID / 세션 UUID |
| status | 공개 상태 |
| main_model_name / model_revision | 시작 시 고정한 모델 alias / 설정 버전 |
| resume_token | 사용자 입력 대기의 현재 토큰. 그 외 null |
| interrupt | 대기 내용 배열. 사용자 화면 또는 Executor 이벤트 대기 |
| failure | 실패 정보 객체 또는 null |
| result | terminal일 때의 Agent 결과 객체, 진행 중 null |
| recovery_required | 실행 종료 확인·복구 필요 여부 |
| checkpoint_run_id / task_id | 호환·진단용 내부 실행 참조. 프론트 재개 ID로 사용하지 않음 |
| attempt_count / next_attempt_at | 내부 실행 시도·다음 시도 시각 |
| cancel_reason / cancel_requested_at | 취소 사유·요청 시각 |
| created_at / updated_at | 접수·최종 변경 시각 |
| started_at / completed_at | 시작·완료 시각 또는 null |

일반 접수 POST는 202와 Location을 반환한다. pending에서 시작하되 동일 key의 재전송은 이미 진행·종료된 상태를 반환할 수 있다. 202는 최종 분석 성공이 아니다.

| status | 의미 |
|---|---|
| pending | Worker 실행 대기 |
| running | Agent 실행 중 |
| waiting_input | 현재 HITL 액션 제출 가능 |
| waiting_executor | 외부 결과 대기. 동일 세션 입력·사용자 resume 금지 |
| success | 정상 종료 |
| error | 실패 종료. 분석 실패도 해당 |
| timeout | 시간 초과 종료 |
| canceled | 취소 완료 |
| recovery_required | 종료가 불확실하여 복구 필요 |

세션 초기 조회의 `active_run`/`availability`는 [세션 API](session-api.md#현재-run과-대화-입력-가능-여부)를 따른다. `available`만 확인하지 말고 allowed_actions의 send_message/respond_to_interaction을 구분한다. 실제 접수는 같은 기준을 session admission 잠금 아래 재검사하며 기존 키/body의 재전송을 먼저 처리한다.

다른 세션은 독립적으로 사용할 수 있다. 같은 세션은 실행·Executor 대기 중 입력을 잠그며 HITL에서는 현재 화면의 액션만 허용한다. 공개 Run 하나 안에서 내부 invocation이 바뀌더라도 run_id는 유지된다. REST 응답·요청·SSE의 실행 식별자는 모두 run_id다. 기존 REST 응답의 id 필드는 제거되었으므로 클라이언트도 run_id를 읽어야 한다. 프로젝트·세션의 id와 SSE 프레임의 id(이벤트 순번)는 그대로다.

## SSE 응답

POST /runs/stream은 일반 POST와 같은 요청·인증·멱등성 규칙을 사용한다. 200 text/event-stream이며 X-Run-Id와 Location으로 접수 ID를 확인한다. GET /runs/{run_id}/stream은 body·Idempotency-Key 없이 기존 Run을 구독한다. 두 stream 모두 Last-Event-ID로 저장 이벤트를 이어 받을 수 있다. 잘못된 POST cursor는 접수 전에 거절한다.

```text
id: 5
event: message.completed
data: {"schema_version":1,"type":"message.completed","sequence":5,"session_id":"22222222-2222-4222-8222-222222222222","run_id":"11111111-1111-4111-8111-111111111111","occurred_at":"2026-10-01T00:00:00Z","data":{"role":"assistant","channel":"commentary","content":[{"type":"text","text":"승인된 분석을 진행하고 있습니다."}]}}

```

저장 이벤트 envelope는 schema_version=1, type, sequence, session_id, run_id, occurred_at, data다. SSE id와 sequence가 같다. 알 수 없는 이벤트는 이미 처리한 순번을 기록하고 무시할 수 있도록 프론트를 구성한다. 같은 sequence를 중복 표시하지 않는다.

| 이벤트 | data와 UI 처리 |
|---|---|
| message.completed | role=user/assistant, channel=answer/commentary, content 배열. 대화/중간 안내 표시 |
| activity.started/completed/updated | kind, title, 이벤트에 따라 activity_id·실행 정보. 진행 표시 |
| interaction.opened/updated | 우측 화면 열기·갱신 |
| interaction.resolved | 해당 화면 닫기. resolution=approved/auto_approved/replanning/answered/rejected 등 |
| run.updated | 상태 변경 알림. 예전 Task 이벤트의 요약일 수 있으므로 Run 상태는 snapshot/GET으로 동기화 |
| run.snapshot | 현재 PublicRunResource 전체를 data에 전달 |

activity.data는 확장 객체이며 모든 이벤트에 activity_id가 있다고 가정하지 않는다. answer 메시지 도착만으로 Run terminal을 판단하지 않는다. LLM 내부 구조화 JSON 토큰, 전체 graph state와 Tool 소스는 공개 SSE에 그대로 보내지 않는다.

run.snapshot은 저장 이벤트가 아니다. schema_version/type/session_id/run_id/cursor/data만 있고 sequence/occurred_at/SSE id가 없다. [snapshot 예제](contracts/agent-api/events/run_snapshot.json) · [필드 주석](contracts/agent-api/events/run_snapshot.jsonc). snapshot.cursor만 보고 이전 저장 이벤트를 모두 처리했다고 판단하지 않는다. 마지막으로 처리한 durable SSE id를 재접속 cursor로 사용한다. `: heartbeat`는 연결 유지용 comment다.

연결 종료는 작업 취소가 아니다. terminal에서 남은 저장 이벤트와 snapshot을 전달한 뒤 stream을 종료한다. 상태 변경 시 DB 원본 이벤트를 읽고 LISTEN/NOTIFY·프로세스 공유 cache로 연결별 반복 SQL을 줄인다. HTTP 대기 중 인증용 DB transaction을 유지하지 않는다.

현재 Run 최초 연결의 인증/소유권을 검사한다. 열린 GET SSE의 쿠키 만료·사용자 비활성화를 이벤트마다 재검증하지는 않는다. 새 요청·재접속은 다시 인증한다.

## 우측 HITL 화면

열린 화면의 data는 interaction_id/revision/kind/status=open/resume_token/summary/payload다. 기존 화면 ID와 revision으로 대상을 식별하고 kind로 UI를 선택한다.

| kind | payload | 사용자 액션 | 예제 |
|---|---|---|---|
| plan_review | plans, notices, revision_policy | 편집·승인·재작성 | [이벤트](contracts/agent-api/events/plan_review.json) · [필드 주석](contracts/agent-api/events/plan_review.jsonc) |
| planning_question | question, notices, revision_policy | 추가 질문 답변 | [이벤트](contracts/agent-api/events/planning_question.json) · [필드 주석](contracts/agent-api/events/planning_question.jsonc) |
| decision_review | decisions | 결과 기반 값 확인 | [이벤트](contracts/agent-api/events/decision_review.json) · [필드 주석](contracts/agent-api/events/decision_review.jsonc) |
| repair_review | 수정 hash·권한·변경 단계·횟수·설명 | 수정 승인·거절 | [이벤트](contracts/agent-api/events/repair_review.json) · [필드 주석](contracts/agent-api/events/repair_review.jsonc) |

GET의 interrupt에서도 사용자 화면을 얻을 수 있다. decision/repair 대기 interrupt에는 task_id/execution_id 등 진단 정보가 추가될 수 있다. Executor 대기의 EXECUTOR_EVENT interrupt는 사용자 승인 화면이 아니다.

plans[]는 PlanView로 제공하며 Workflow 원본과 다르다.

| PlanView 필드 | 의미 |
|---|---|
| plan_id / plan_revision | 후보 식별자·편집 버전 |
| workflow_id / definition_version | 원본 정의 식별자·버전 |
| name / goal | 계획명·목표 |
| execution_kind / workflow_eligible / approval_mode | registered/free_code, Workflow 대상 여부, user/configuration 승인 |
| skills | skill_id/name/description |
| inputs | name/title/description/kind/required/editable/value_schema/origin/has_value/value |
| steps | step_id/skill_id/tool_id/function_name/description/depends_on/parameters/when/status |
| decisions | decision_id/evidence_steps/guidance/value_schema/status=deferred |
| execution | mode/repair_level/max_repair_attempts/review_mode/review_interval_tools와 사용자 변경 상한 |
| outputs | output_id/kind/description/status |

inputs.origin은 agent/workflow_default/user/unresolved다. has_value=true이면 기본값으로 표시하고 false이면 미확정으로 표시한다. null과 미입력은 다르다. editable=false는 읽기 전용이다.

parameters.kind는 workflow_input/literal/step_reference/deferred/system_context다. 해당 kind에 따라 input_name, value/value_schema, step_id/selector, decision_id/guidance, context_key가 제공된다. null인 선택 필드는 view에서 생략될 수 있다. Skill·함수명·설명·파라미터는 보이고 Python 소스는 보이지 않는다.

repair payload에는 proposal_sha256/attempt/max_attempts/authorized_level/required_level/requires_policy_escalation/summary/failed_step_ids/completed_step_ids/changed_step_ids/steps/source_modified/workflow_eligible/validation_scope가 있다. decision payload 각 항목에는 decision_id/guidance/evidence_steps/value_schema/has_value와 선택적 value가 있다.

## 최종 결과

terminal의 result는 route/final_response/service_response/workflow_origin/selected_workflow_id를 담는다. 후자의 호환 필드는 null일 수 있다. 최종 답변은 result.final_response와 SSE answer 메시지로 전달한다.

| final_response.status | 내용 |
|---|---|
| answer | message 문자열. FAQ·결과 설명·보고서 편집 등 대화 답변 |
| plan_approved | approved_plan. Executor 제출 비활성화 환경의 승인 종료 |
| analysis_completed | execution_id/executor_status/planning/observations/skipped_steps/repair/report |
| analysis_failed | 같은 실행 결과 구조. 실패·부분 성공을 보존하고 상위 Run은 error |

planning은 revision_count/execution_kind/approval_mode/workflow_eligible다. observations 각 항목은 step_id/tool_id/status/summary/has_image/incomplete/repair_attempt/error다. 전체 DataFrame·원본 코드·파일 경로 대신 제한된 관찰 요약을 제공한다. error는 공개용 일반 설명이며 원문 traceback은 내부 기록에 보관한다.

repair는 attempts/max_attempts/authorized_level/stop_reason/history/workflow_eligible다. history에는 attempt/required_level/summary/changed_step_ids/status/outcome이 있다.

report는 보고서를 요청한 경우 format=markdown/content/evidence_steps/status/validation_scope/artifact_registration을 담고, 미요청이면 null이다. status=ready 또는 모델 해석 검증 실패 시 evidence_only, artifact_registration=deferred가 현재 동작이다. HTML 선언 가능 여부와 현재 렌더러 지원은 [Workflow 문서](workflow-json-reference.md)를 따른다. Artifact 자동 등록 완료로 해석하지 않는다.

최종 보고서는 Executor terminal 확인 뒤 작성한다. MULTI는 Finalize 접수만으로 끝났다고 표시하지 않는다. 결과 설명·보고서 편집만이면 새 Executor를 만들지 않으며 새 계산은 새 Run·새 Execution으로 진행한다.

## 목록과 로그와 취소

목록 query: limit 기본 50/1~200, cursor, sort=-created_at 또는 created_at, created_at_from 이상/created_at_to 미만. 응답은 items 배열과 page.has_next/page.next_cursor다. items는 PublicRunResource이며 전체 승인 구간을 한 공개 Run으로 묶는다.

로그 응답은 배열이고 log_id/run_id/event_key/agent_name/node/event/kind/payload/created_at을 담는다. 공개 run_id로 모아 반환하며 payload는 유연한 진단 객체다. 페이지 옵션은 없다. 사용자 UI의 주요 진행 표시는 SSE를 사용한다.

취소 body는 선택적 reason(최대 1000자)을 갖는다. [예제](contracts/agent-api/requests/cancel.json) · [필드 주석](contracts/agent-api/requests/cancel.jsonc). 202는 취소 요청 수락이며 실제 실행 중이면 협조적 종료를 확인한다. waiting_executor의 일반 cancel은 409로 거절한다. 이미 canceled이면 같은 상태를 반환하고 다른 terminal이면 409다. 로그아웃·쿠키 만료·SSE 단절은 작업 취소가 아니다.

## 오류와 현재 명세 제한

| HTTP | 주요 원인 |
|---|---|
| 400 | Idempotency-Key 누락, 잘못된 Last-Event-ID/목록 cursor |
| 401 | 쿠키 누락·만료·비활성 사용자 |
| 403 | CSRF 검증 실패 |
| 404 | 접근 가능한 세션/Run 부재 |
| 409 | 세션 점유, 오래된 token/revision, key 충돌, 취소 불가능 |
| 422 | body/schema/편집/승인 오류, 미지원 첨부 |
| 503 | 로그인 저장소 장애, SSE 종료 중·연결 한도 초과 |

일반 오류는 detail 문자열, FastAPI 입력 검증 오류는 detail 배열이다. SSE 한도 초과는 Retry-After:5를 제공한다. stream 접수 후 연결 한도로 거절되더라도 Run은 이미 접수됐을 수 있으므로 동일 key·body로 확인한다. 새 key로 무조건 재실행하지 않는다.

현재 자동 OpenAPI는 SSE 응답을 application/json으로 표시하고 302 로그인도 JSON으로 표시한다. 실제 코드는 SSE text/event-stream과 브라우저 redirect다. 중첩 result/interrupt/event.data도 모두 엄격한 OpenAPI 응답으로 선언되지 않았다. 첨부 [OpenAPI snapshot](contracts/agent-api/openapi.snapshot.json) · [필드 주석](contracts/agent-api/openapi.snapshot.jsonc)은 이 제한을 보존하며, [추가 public payload schema](contracts/agent-api/payload-schemas.json) · [필드 주석](contracts/agent-api/payload-schemas.jsonc)와 실제 구현을 함께 확인한다. 이 문서 작업은 런타임 API annotation을 변경하지 않는다.

기존 1.3 Graph/checkpoint에서 새 planning Runtime으로 자동 재개를 보장하지 않는다. 이전 미종료 Run을 정리하고 Graph 버전·자산 호환을 확인하여 전환한다. 실제 사내 SSO 왕복과 Gaia 제공 router는 별도 환경 검증이 남아 있다.

## 구현 근거

- 라우터: [runs.py](../src/api_service/api/v1/routes/runs.py)
- 요청: [run_request.py](../src/service_contracts/run_request.py)
- Run 응답: [run_schema.py](../src/api_service/schemas/common/run_schema.py)
- 상태·재개 검증: [public_run_service.py](../src/api_service/services/public_run_service.py)
- 화면·액션: [plan_interaction.py](../src/service_contracts/plan_interaction.py), [execution_repair.py](../src/service_contracts/execution_repair.py)
- SSE: [run_stream_service.py](../src/api_service/services/run_stream_service.py)

## 프로젝트 메모리 갱신 결과 051

Run 요청·재개·Run 식별자는 그대로다. 프로젝트 메모리 관리는 `/projects/{project_id}/memory`의 동일 경로의 GET·PUT·DELETE로 제공한다. 프로젝트당 하나의 Markdown content와 문서 version을 사용하며, PUT은 전체 문서 수정, DELETE는 버전을 증가시키는 초기화다. `auto_context`에서 실제 메모리 갱신을 시도한 경우 활동 이벤트의 `kind=project_memory`와 답변 최종 결과의 `final_response.project_memory`에 saved/not_saved 결과를 전달한다. 실패·동시 갱신 충돌을 저장 성공으로 표시하지 않는다. 내부 model의 memory_updates는 프론트가 전달하는 필드가 아니다. [각 필드와 설정](project-memory.md)을 참고한다.
