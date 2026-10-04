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

075가 함께 배포되는 경우에는 해당 메모리 migration 절차를 별도로 따라야 한다. 세션 입력 가능 여부·진행 Run을 resource에 표현하는 계약은 다음 리뷰에서 정하며, 현재 API에는 새 상태 필드를 추가하지 않았다.
