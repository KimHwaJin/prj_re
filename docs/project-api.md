# 프로젝트 관리 API

프로젝트는 한 사용자의 세션을 묶는 단위다. `system_prompt`는 공통 지침이고 `project_memory`는 별도 API·LangGraph Store에서 관리하는 참고 문서다. 일반 프로젝트 API는 로그인한 사용자의 활성 프로젝트만 다룬다. 관리자 역할이나 member 테이블의 존재를 타 사용자 프로젝트 조회 권한으로 해석하지 않는다.

기본 prefix는 `/api/v1`이다. SSO 쿠키로 인증하고 POST/PATCH/DELETE는 `X-CSRF-Token`도 필요하다. body에 호출자 user_id를 받지 않는다. [사용자·SSO 계약](user-identity-api.md), [프로젝트 메모리](project-memory.md)를 함께 참고한다.

| Method | Path | 요청·응답 |
|---|---|---|
| POST | /projects | ProjectCreate → 201 ProjectResource, Location 제공 |
| GET | /projects | Page[ProjectSummary]. 본인의 활성 프로젝트, 기본50·최대200 |
| GET | /projects/{project_id} | ProjectResource 단건 상세 |
| PATCH | /projects/{project_id} | ProjectUpdate → 200 ProjectResource |
| DELETE | /projects/{project_id} | 보호 조건 검사 후 soft delete, 204·body 없음 |

## 생성·수정 요청

생성 예제:

```jsonc
{
  // 변경/생성할 프로젝트 이름. 연속 공백을 정규화한다. 기본 프로젝트의 이름은 바꿀 수 없다.
  "project_name": "품질 분석",
  // 사용자 지정 프로젝트 공통 지침. 새 Run 실행 시작 시 고정하며 기존 Run 재개에서는 변경하지 않는다. 빈 문자열은 지침 없음이다. 수정에서 생략은 유지, 명시적 null은422, 빈 문자열은 초기화다.
  "system_prompt": "보고서는 핵심 결론과 분석 근거부터 설명하세요."
}
```

project_name은 필수 문자열·최대200자이며 앞뒤/연속 공백을 정규화한다. 빈 문자열·공백뿐이면 기존 기본 프로젝트 충돌 정책대로409다. 다른 활성 프로젝트와 대소문자를 무시한 이름이 중복되면409다. 신규 일반 프로젝트는 is_default=false이며 owner membership을 같은 transaction에서 생성한다.

system_prompt는 생략하면 빈 문자열이고 null은422다. 공백·줄바꿈을 임의로 정규화하지 않고 그대로 저장한다. 새 프로젝트의 prompt_version은1이다. 두 필드 외 user_id, id, project_id, is_default, prompt_version, project_memory 등 임의 필드는422다.

지침을 초기화하는 수정 예제:

```jsonc
{
  // 사용자 지정 프로젝트 공통 지침. 새 Run 실행 시작 시 고정하며 기존 Run 재개에서는 변경하지 않는다. 빈 문자열은 지침 없음이다. 수정에서 생략은 유지, 명시적 null은422, 빈 문자열은 초기화다.
  "system_prompt": ""
}
```

| PATCH 값 | 처리 |
|---|---|
| project_name 생략 | 이름 유지 |
| 유효한 project_name 지정 | 공백 정규화 후 변경. 동일 프로젝트 이름 재지정 가능 |
| project_name:null/빈 문자열/공백뿐 | 422. 기본 프로젝트 이름 변경 요청은409 |
| system_prompt 생략 | 지침 유지 |
| system_prompt:"" | 지침 초기화 |
| system_prompt:null | 422 |
| {} 또는 알 수 없는 필드 포함 | 422, 전체 거절 |

PATCH는 적어도 하나의 편집 필드를 지정해야 한다. 명세도 minProperties=1·문자열·additionalProperties=false로 표현하며 null 허용 선택 구조를 남기지 않는다. 지침 내용이 실제로 바뀔 때만 prompt_version이1증가한다. 이름만 변경하거나 같은 지침을 다시 보내면 버전은 유지한다. 이미 빈 지침에 빈 문자열을 보내도 유지한다. Project API로 버전별 지침 이력을 조회하는 기능은 없다.

기본 프로젝트는 이름 변경·개별 삭제를 금지하지만 지침 변경은 가능하다. 이름과 지침을 함께 보내서 이름이 잘못됐거나 금지됐다면 지침도 부분 반영하지 않는다. 실행 중 이름·지침 편집은 기존 정책대로 가능하며 진행 중인 Run은 아래 snapshot 규칙을 따른다.

## 목록과 상세 필드

| 필드 | 목록 | 상세·생성·수정 | 의미 |
|---|---|---|---|
| id | 포함 | 포함 | 프로젝트 UUID. path의 project_id로 사용 |
| name | 포함 | 포함 | 표시 이름. request의 project_name에 대응 |
| is_default | 포함 | 포함 | 기본 프로젝트 여부 |
| created_at | 포함 | 포함 | 생성 시각 |
| updated_at | 포함 | 포함 | 프로젝트 변경 시각. 최근 대화/실행 시각은 아님 |
| system_prompt | 제외 | 포함 | 공통 지침 전체. 빈 문자열은 지침 없음 |
| prompt_version | 제외 | 포함 | 지침 변경 버전 |

목록에는 세션/메시지·메모리·실행 결과·사용자 로그인 정보가 없다. items/page 형식을 유지한다. 상세의 기존 id/name 필드와 요청의 project_name은 유지한다. GET 목록·상세에는 Cache-Control:no-store를 적용한다.

목록 query는 limit(기본50·1~200), sort(-created_at 기본 또는 created_at), cursor, created_at_from(포함), created_at_to(제외)다. 동시각은 프로젝트 UUID로 순서를 확정한다. 하한≥상한·잘못된 limit/sort는422, 잘못된 cursor는400이다. 다음 페이지는 같은 정렬·날짜 조건으로 next_cursor를 그대로 보낸다. 총개수 COUNT와 페이지 전체 snapshot은 제공하지 않는다. cursor는 시각/UUID 인코딩값이며 암호화된 인증 토큰이 아니다.

목록 SQL은 5개 필드만 읽고 전체 ORM/system_prompt를 가져오지 않는다. 인증1+페이지1의2 SELECT, 최대 limit+1행이다. 프로젝트마다 하위 세션/메시지를 추가 조회하지 않는다. 읽는 열·응답 필드 축소를 검증했으며 실제 처리량/지연 전후 수치는 이번에 측정하지 않았다.

- [생성 요청](contracts/agent-api/requests/project_create.json) · [필드 주석](contracts/agent-api/requests/project_create.jsonc)
- [지침 초기화 요청](contracts/agent-api/requests/project_update.json) · [필드 주석](contracts/agent-api/requests/project_update.jsonc)
- [목록 응답](contracts/agent-api/responses/project_list.json) · [필드 주석](contracts/agent-api/responses/project_list.jsonc)
- [상세 응답](contracts/agent-api/responses/project_detail.json) · [필드 주석](contracts/agent-api/responses/project_detail.jsonc)

## Agent 적용 시점

현재 비동기 Run의 지침/버전은 **Worker가 새 Run을 실행하기 직전** DB에서 읽어 checkpoint 상태에 고정한다. HTTP POST 접수 시점에 고정하지 않는다. 큐에서 기다리는 동안 지침을 바꾸면 실행 시작 시 읽은 최신 값이 적용된다. 이후 HITL·Executor 재개와 영속 receipt의 완료 복구에서는 해당 Run의 값을 유지한다. snapshot 조회용 서비스 DB 연결은 그래프·LLM/Executor I/O 전에 반환한다.

Run A를 접수한 뒤 실행 전에 지침을 v2로 바꾸면 A는 v2로 시작한다. A가 HITL 대기하는 동안 v3로 바꿔도 A의 승인/실행/보고는 v2를 사용한다. A 종료 후 Run B는 v3를 읽는다. 같은 세션에서 지침을 빈 문자열로 초기화한 뒤 새 Run을 시작해도 이전 지침을 상속하지 않는다.

각 역할의 create_agent에 연결된 ProjectPromptMiddleware는 저장된 지침을 모델 호출·재시도마다 추가한다. AgentContext를 이용하며 공유 Agent 객체의 전역 프롬프트를 바꾸지 않아 다른 프로젝트와 섞지 않는다. 지침이 빈 문자열이면 추가하지 않는다. 과거 checkpoint에 project_system_prompt 키가 아예 없으면 기존 legacy backfill이 최초 재개 시 DB 값을 읽어 보완한다. 키가 있고 값이 빈 문자열이면 이미 확정된 지침 없음으로 취급하여 다시 읽지 않는다.

project_memory의 문서 version·선택/갱신 정책은 지침 버전과 별개다. system_prompt PATCH로 메모리를 수정하지 않는다. 실제 모델이 지침을 의미적으로 잘 따르는지는 이번 mock 검증의 보장 범위가 아니다.

## 삭제와 권한

기본 프로젝트 삭제는409다. 일반 프로젝트도 하위 세션에 큐/실행/HITL/Executor 대기·복구 필요·미확인 실행 점유가 있으면409다. 잠금과 같은 transaction에서 검사하며 숨겨진 과거 세션의 미종료 작업도 확인한다. 삭제로 외부 Executor를 임의 취소하거나 종료를 선언하지 않는다.

성공하면 Project와 활성 하위 Session/Message를 soft delete하고204를 반환한다. 별도 삭제 통계 객체는 반환하지 않는다. Run/Task/checkpoint 이력을 물리 삭제하지 않는다. 없는/삭제된/타 사용자 프로젝트는404다. 로그인 없음/삭제 사용자는401, 변경 요청 CSRF 불일치는403이다. 사용자 전체 삭제의 cascade는 별도 정책이다. [CRUD 보호](crud-lifecycle-policy.md)를 따른다.

083은 DB migration·환경변수·의존성을 추가하지 않는다. 기존 프론트가 목록에서 지침/버전을 읽었다면 선택한 프로젝트의 상세 GET으로 바꿔야 한다. null/임의 필드가 섞인 PATCH는 필드 생략 또는 빈 지침으로 이행해야 한다.
