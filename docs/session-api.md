# 세션 관리 API와 설정

세션은 한 프로젝트에 속한 채팅방이다. 생성 시 프로젝트와 실행 커널을 정하고, 이름만 변경할 수 있다. 소속 이동과 설정 PATCH는 지원하지 않는다. 인증은 기존 SSO 쿠키·쓰기 CSRF·활성 사용자/프로젝트 소유권 경계를 따른다. 모든 경로의 prefix는 `/api/v1`이다.

| Method | Path | 의미 |
|---|---|---|
| POST | /projects/{project_id}/sessions | 해당 프로젝트의 새 세션 생성 |
| GET | /projects/{project_id}/sessions | 프로젝트 세션 목록, 기존 cursor 페이지네이션 |
| GET | /sessions/{session_id} | 세션 메타데이터 조회; 대화 이력은 별도 messages API |
| PATCH | /sessions/{session_id} | 이름 변경만; 실행 중에도 허용 |
| DELETE | /sessions/{session_id} | 미종료 작업이 없을 때 세션/메시지 숨김, 204 |

## 생성 요청

```jsonc
{
  // 생략/null/공백 이름은 기존 정책대로 '새 대화'. 최대 300자.
  "session_name": "불량 원인 분석",
  "settings": {
    // 실제 Executor의 runtime.profile. 문자열 1~128자, 영문/숫자/밑줄/점/하이픈.
    // 생략하거나 null이면 EXECUTOR_RUNTIME_PROFILE을 확정하여 저장한다.
    // 서비스의 EXECUTOR_RUNTIME_PROFILES 목록에 있어야 한다.
    "kernel_profile": "default"
  }
}
```

settings 자체를 생략하거나 `{}`로 보내도 된다. settings=null/배열/문자열은 422다. kernel_profile의 숫자·빈 문자열·공백·경로 형식과 허용되지 않은 이름은 생성 시 422다. 요청 최상위 및 settings의 알 수 없는 필드도 422다. main_model_name, 실행 mode, repair_level, project_id/target_project_id를 settings나 임의 필드로 받지 않는다. 잘못된 요청은 세션을 생성하지 않는다. 편의 Message API가 내부적으로 생성하는 세션에도 동일한 기본값 확정·검증을 적용한다.

생성 성공은 201이며 Location 헤더는 `/api/v1/sessions/{id}`다. 아래 resource는 생성/단건 조회/이름 변경 응답에 사용하며 목록은 `{items:[...],page:{next_cursor,has_next}}`다.

```jsonc
{
  // 세션 ID. Run 요청·조회·대화 이력 API의 session_id로 사용.
  "id": "11111111-1111-4111-8111-111111111111",
  // 세션 생성 시 정한 프로젝트 ID; 공개 API로 이동 불가.
  "project_id": "22222222-2222-4222-8222-222222222222",
  // 표시 이름. 입력 session_name과 대응한다.
  "name": "불량 원인 분석",
  // 현재 대화 이력의 leaf 메시지. 실행 중인 Run ID나 입력 가능 여부가 아니다.
  "current_leaf_message_id": null,
  "settings": {
    // 요청에서 생략했어도 새 세션에는 적용된 값이 저장되어 응답에 포함됨.
    "kernel_profile": "default"
  },
  // 현재 진행 중인 공개 Run. 내부 invocation/Executor execution ID가 아니다.
  // 아래 예제는 새 세션이므로 null. 상세 HITL/결과는 Run GET/SSE에서 받는다.
  "active_run": null,
  "availability": {
    // 대화 입력 능력. 이름 변경/삭제/관리 권한 판정과 별개다.
    "status": "available",
    // 새 요청 시작만 가능. HITL에서는 respond_to_interaction만 제공한다.
    "allowed_actions": ["send_message"],
    // 입력 가능할 때 null. busy/blocked에서는 기계적으로 처리할 사유 코드.
    "reason": null
  },
  // 세션 생성/수정 시각. 메모리/Run 상태 변경 시각과 별개.
  "created_at": "2026-10-04T09:00:00Z",
  "updated_at": "2026-10-04T09:00:00Z"
}
```

PATCH는 `{"session_name":"변경한 이름"}`으로 보낸다. settings나 프로젝트 변경 필드를 함께 보내면 422로 전체 거절하고 이름을 부분 반영하지 않는다. 빈/공백 이름은 422다. DELETE의 미종료·HITL·WAITING_EXECUTOR 보호와 404/409는 [CRUD 정책](crud-lifecycle-policy.md)을 따른다.

## 값의 수명

| 값 | 입력 위치 | 유지 범위 |
|---|---|---|
| kernel_profile | 세션 생성 settings | 세션에 저장, Worker → graph → 승인 snapshot → Executor runtime.profile |
| main_model_name | 새 Run 요청 | 해당 Run에 고정, resume에서 변경 불가 |
| mode/repair_level 등 | 계획 선택·편집·승인 | 기존 Workflow/서버 상한과 승인 정책 |
| Worker concurrency/DB pool 등 | 서비스 config | 프로세스 설정, 사용자 요청에서 변경 불가 |

새 세션의 기본 profile을 생성 시 저장하므로 서비스 기본값이 바뀌어도 기존 세션은 변경되지 않는다. 생성 허용 목록에서 profile을 제거해도 기존 DB/승인 snapshot의 값을 다른 profile로 대체하지 않는다. 해당 커널을 Executor에서 실제로 제거하면 기존 실행은 Executor에서 실패할 수 있으므로 서비스/Executor 설정은 함께 관리해야 한다.

## 중앙 설정

```yaml
service:
  executor:
    # 세션에서 profile을 생략했을 때 사용할 값. 실제 등록한 profile로 지정.
    executor_runtime_profile: default
    # 생성 요청에서 선택 가능한 profile. 기본 profile을 반드시 포함.
    # Executor의 RUNTIME_ALLOWED_PROFILES 및 Target/Jupyter 지원과 맞춘다.
    executor_runtime_profiles: [default, "3102311"]
```

config > env > 기본값 우선순위를 유지한다. env는 `EXECUTOR_RUNTIME_PROFILE`과 JSON 배열 문자열인 `EXECUTOR_RUNTIME_PROFILES='["default","3102311"]'`이다. 목록 미설정 시 기본 profile 하나만 허용한다. 빈 목록·중복·잘못된 형식·기본값 누락은 시작 시 설정 오류다. 기존 코드의 미설정 기본 profile `ml`은 이번 작업에서 바꾸지 않았다. 실제 Executor가 default/3102311만 제공한다면 위처럼 default를 명시해야 한다. 변경 후 프로세스를 재시작한다.

세션 생성마다 Executor/주피터를 조회하지 않는다. 로컬 설정 검증은 실제 원격 커널 등록·가용성의 증명이 아니며 Executor가 실제 접수를 검증한다. 이 목록은 모든 정상 인증 사용자의 서비스 허용 범위이고, 사용자별 커널 선택 권한이나 커널 목록 API는 이번 범위에 추가하지 않았다.

## 기존 DB 데이터

077은 DB 구조 migration을 요구하지 않는다. 새로운 세션부터 검증된 settings/kernel_profile을 저장한다. 과거 세션의 자유 JSON은 조회/이름 변경으로 삭제하거나 재작성하지 않아 응답 settings는 현재도 객체 형태다. 과거에 profile이 없거나 null인 세션은 기존 fallback 동작을 유지하며 생성 시 고정 보장은 소급 적용하지 않는다. 잘못된 과거 profile은 graph 전달 전에 방어적으로 검사해 거절한다. 기존 값의 실제 Executor 지원 여부를 확인하거나 일괄 보정하는 작업은 수행하지 않았다.

075가 함께 배포되는 경우에는 해당 메모리 migration 절차를 별도로 따라야 한다. 078에서 세션 activity/availability 응답과 실제 접수 기준을 추가했다. 078 자체의 DB 구조 migration이나 새 환경 설정은 없다.


## 현재 Run과 대화 입력 가능 여부

생성·단건 조회·목록 항목·이름 변경 응답에 동일한 active_run/availability를 제공한다. 별도 Session 상태 머신을 저장하지 않으며, 한 SQL snapshot에서 최신 invocation·Task·실행 점유·미완료 내부 명령·LLM 상태를 판정한다. checkpoint와 실제 모델/Executor는 조회하지 않는다. 목록도 페이지 전체를 한 SQL로 읽고 Message 이력·전체 Run 결과/metadata를 가져오지 않는다. 완료된 Task에 속한 모든 과거 Run의 최신 호출을 일일이 조회하지 않도록 미종료 후보부터 선택한다.

active_run의 run_id는 HITL resume마다 유지되는 공개 Run UUID이며 status는 상세 PublicRunResource와 같은 의미다. active_run은 정상적인 진행 Run이 없으면 null이다. 비정상 상태에서 Task/점유만 남으면 null인데도 busy/blocked일 수 있으므로 **null만 보고 입력을 열지 않는다**. 여러 공개 Run이 동시에 미종료인 불일치 상태에서는 대표 Run을 표시하더라도 blocked로 응답하고 임의로 재개하지 않는다. 마지막 완료 Run을 찾는 용도라면 별도 Run 목록을 사용한다.

| 상황 | availability.status | allowed_actions | reason |
|---|---|---|---|
| 미종료 작업·실행 점유 없음 | available | [send_message] | null |
| 현재 HITL이며 점유/명령 처리까지 종료됨 | available | [respond_to_interaction] | null |
| 접수 대기·실행 중·후처리/명령 점유 중 | busy | [] | processing |
| Executor 등 외부 결과 대기 | busy | [] | waiting_external |
| 취소 요청 후 실제 종료 전 | busy | [] | canceling |
| 복구 플래그 또는 여러 활성 Run의 불일치 | blocked | [] | recovery_required |
| 생성/이름 변경 commit 직후 다른 요청이 삭제함 | blocked | [] | resource_unavailable |

send_message는 `POST /sessions/{session_id}/runs`의 새 input을 보내는 액션이다. respond_to_interaction은 **같은 POST**에서 현재 Run의 run_id/resume_token/command를 보내는 액션이다. 현재 인터랙션의 수정·선택·승인·피드백도 이 액션에 포함하며, 그 액션의 구체적 허용 범위는 Run의 interaction payload와 기존 revision 검사로 결정한다. HITL의 available은 새 Run을 시작해도 된다는 뜻이 아니다. 복구 중인 Task/owner/command가 있으면 자동으로 입력을 열지 않고, owner의 heartbeat가 오래되어도 점유를 만료 취급하지 않는다.

Run이 waiting_input으로 바뀌어도 Worker의 실행 점유나 내부 명령이 아직 정리 중인 짧은 구간에는 busy일 수 있다. Run이 terminal이어도 점유 해제 전에는 busy다. 읽기 결과는 잠금 예약이 아니며 POST는 기존 session admission 잠금 아래 최신 상태를 다시 검사한다. 그 사이 상태가 바뀌면 409이므로 화면을 갱신한다. 같은 Idempotency-Key/body의 재전송은 새로운 입력이 아니므로 busy/blocked에서도 기존 접수를 재사용한다. 다른 body·오래된 token/revision·다른 Run의 token은 기존 규칙대로 거절한다.

프론트는 최초 열기·새로고침·로그인 복귀 때 Session GET의 active_run으로 기존 Run GET/SSE에 연결하고 상태·중간 메시지·HITL·최종 결과를 받는다. 기존 Run SSE 계약은 변경하지 않았다. Session GET을 새 상시 폴링 채널로 쓰지 않는다. 409의 상태 재확인이 필요하면 Session/Run을 다시 읽는다. 이름 변경은 대화 busy와 별개로 기존 규칙대로 가능하고, 삭제는 HITL을 포함한 미종료 작업 전체가 끝나야 가능하다.

생성/이름 변경이 commit된 뒤 동시 삭제가 발생하면 그 성공 응답은 201/200을 유지하면서 resource_unavailable로 입력을 막는다. 이후 일반 GET/목록에서는 삭제된 세션을 숨긴다. 세션 응답의 updated_at은 Session 메타데이터의 수정 시각이므로 Run/availability 변화 시각을 나타내지 않는다.
