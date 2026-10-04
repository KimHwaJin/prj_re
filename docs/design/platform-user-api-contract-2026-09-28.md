# X-User-Id 공통 사용자 식별 및 관리 API 계약

> 2026-10-01 사용자 식별 변경: 아래 `X-User-Id` 계약은 이전 결정 기록이다. 현재 서비스는 SSO 로그인 쿠키와 변경 요청의 `X-CSRF-Token`을 사용하며, [현재 SSO 계약](../sso-authentication.md)을 우선한다. Run/계획/데이터 body와 내부 UUID 소유권은 유지한다. Gaia body의 user_id를 검증된 로그인 신원으로 신뢰하지 않는다.

2026-09-28 · 사용자 합의 반영. 설계 확정 기록이며 API 구현 완료가 아니다. 추가된 프로젝트 공유 컨텍스트와 전체 결정은 [최종 결정](crud-final-decisions-2026-09-28.md)을 따른다.

## 확정 전제

플랫폼과 독립적으로, 비밀번호 없이 등록된 사용자 ID만 입력하여 서비스를 이용한다. 모든 사용자용 API는 **X-User-Id 헤더**로 호출자를 식별한다. 호출자 ID를 body/query로 반복 전달하거나 로그인·Bearer 토큰 발급 API를 만들지 않는다. 이전 문서의 플랫폼 인증 필수 연계 및 메서드별 body/query 제안은 이 결정으로 대체한다.

등록된 활성 사용자인지 확인하고 DB의 admin/user 역할과 자원 소유권·공유 권한을 적용한다. 전달된 ID를 호출자의 신원으로 신뢰하는 방식이며, ID 소유 증명을 추가하는 요구는 없다. 호출자의 역할을 요청 role 값으로 덮어쓰지 않는다. 관리자용 사용자 등록/수정 body의 role은 대상 사용자의 속성으로 허용한다.

| 입력 | 의미 |
|---|---|
| X-User-Id | 호출하는 사용자 |
| path의 user_id/project_id/session_id 등 | 조회·변경할 대상 자원 |
| POST /users body의 user_id | 새로 등록할 사용자 ID. 호출자와 다른 역할의 필드 |
| PATCH body | 허용된 변경 속성. 일반 자원의 소유자를 바꾸는 용도로 사용하지 않음 |

헬스체크·메트릭·Executor 내부 이벤트 등 사용자 호출이 아닌 경로는 각자의 접근 규칙을 유지한다. 내부 Worker/Executor 이벤트에 프론트의 사용자 헤더를 강제하지 않는다.

## 사용자 생애주기

- 최초 관리자는 배포 초기화 명령으로 한 번 등록한다. 같은 명령의 재실행으로 중복 생성하지 않는다. 공개된 최초 관리자 가입 API를 만들지 않는다.
- 사용자 등록은 관리자 전용 POST /users에서 수행한다. body의 role은 admin/user를 허용하고 생략 시 user를 기본값으로 한다. 사용자와 기본 Project를 한 transaction으로 생성한다.
- GET /users/me는 헤더의 사용자 이름·역할·기본 프로젝트를 조회한다. 로그인, 토큰 발급, 미등록 사용자 자동 생성은 수행하지 않는다.
- 사용자 수정·삭제는 관리자 전용이다. 일반 사용자는 본인 정보 조회와 본인 업무 자원 관리만 수행한다.
- DELETE /users/{user_id}는 사용자와 소유 프로젝트·세션·메시지를 함께 soft delete하여 사용자 요청과 일반 조회를 차단한다. 사용자 요구에 따라 하위 데이터 숨김을 유지한다. 미종료 작업이 있으면 삭제를 거절하고, 작업 종료 후 함께 숨긴다. 숨김만으로 외부 실행 종료를 선언하지 않는다. 물리 데이터 제거를 의미하지 않는다.
- 마지막 활성 관리자 삭제·강등을 막는다. 최초 사용자 등록·역할 변경의 동시 요청에서도 규칙을 유지한다.
- 공개 user_id는 사람이 입력할 문자열 ID로 확정한다. 내부 UUID와 기존 FK는 유지하고 문자열 식별자를 연결한다. X-User-Id와 사용자 관리 API의 공개 대상 ID는 문자열 ID를 사용한다.

## API별 사용자 전달

모든 경로는 /api/v1 prefix이며, 아래 사용자용 요청은 모두 X-User-Id가 필수다.

| API | 권한/처리 |
|---|---|
| POST /users | 관리자: body에 등록 대상 문자열 ID·이름·role(admin/user) |
| GET /users/me | 활성 사용자: 헤더의 본인 정보 |
| GET /users/{user_id} | 대상 사용자 조회 정책에 따라 제한; 일반 프론트는 /me 사용 |
| PATCH /users/{user_id} | 관리자: 대상 이름·허용된 역할 변경 |
| DELETE /users/{user_id} | 관리자: 대상 사용자·하위 데이터 숨김, 마지막 관리자 보호 |
| Project 생성·목록·상세·수정·삭제 | 호출자의 소유권 + 삭제/설정 변경 조건 |
| Session 생성·목록·상세·수정·삭제 | 부모/대상 소유권 + 실행 상태에 따른 이동·삭제 조건 |
| Message 조회 | 소속 세션 소유권. CUD 공개 라우트 제외, 내부 저장 기능 유지 |
| Run 생성 | 세션 소유권 + 실행 점유, agent_id와 선택 main_model_name |
| Runs로 통합할 resume/cancel | 같은 공개 run_id 유지, 소유권 + 현재 interrupt/상태 검사. 기능 이관 후 Tasks API 제거 |
| Run 목록·상세·로그·SSE | 소유권 + 페이지/이벤트 계약. 전체 작업/실행 구간의 ID 연결은 보존 |
| Workflow 조회·생성·수정·승격·복제·삭제 | 일반 사용자도 승격 가능. Executor 성공 조건 없음. 자산 유효성·소유권·공개 범위 검사 |
| Jupyter 조회·점검 | 해당 사용자의 접근 권한 |

관리자 역할만으로 다른 사용자의 모든 대화를 자동 공개하지 않는다. 필요한 관리 기능마다 권한을 정한다. body/query에 같은 호출자 ID를 다시 요구하지 않으며, 자원의 대상 ID를 아는 것만으로 접근을 허용하지 않는다.

## 요청 예시

```http
POST /api/v1/users
X-User-Id: admin-001
Content-Type: application/json

{"user_id":"user-001","user_name":"홍길동","role":"user"}
```

```http
GET /api/v1/users/me
X-User-Id: user-001
```

```http
POST /api/v1/projects
X-User-Id: user-001
Content-Type: application/json

{"project_name":"분석 프로젝트"}
```

```http
GET /api/v1/projects?limit=50
X-User-Id: user-001
```

```http
PATCH /api/v1/sessions/{session_id}
X-User-Id: user-001
Content-Type: application/json

{"session_name":"매출 분석"}
```

```http
DELETE /api/v1/sessions/{session_id}
X-User-Id: user-001
```

```http
POST /api/v1/sessions/{session_id}/runs
X-User-Id: user-001
Content-Type: application/json
Idempotency-Key: new-interaction-123

{"agent_id":"analysis","message":"매출을 분석해줘","main_model_name":"configured-model-b"}
```

위 Run 예시는 목표 접수 계약이다. 현재 RunCreate를 이미 변경했다는 뜻이 아니다. SSE 호출 클라이언트도 사용자 헤더를 전달할 수 있는 방식을 사용하도록 통합 검증한다.

## 서버 내부 적용

공통 dependency가 헤더 → 등록/활성 사용자 조회 → 내부 Actor(user_id, role)로 변환한다. 서비스는 Actor를 받아 소유권과 허용 동작을 검사한다. 사용자 조회와 역할 처리를 라우터마다 복제하지 않는다. /users/me는 동적 /users/{user_id} 경로보다 먼저 등록한다.

Worker에는 접수 시 확정한 내부 사용자 ID를 저장해서 전달한다. resume는 호출자가 작업 소유자인지 확인하며 원래 Agent/모델/checkpoint를 복원한다. 자원 수정 DTO의 전체 내용을 ORM에 그대로 대입하여 소유자가 바뀌지 않도록 허용 필드를 명시한다.

## LLM 선택


- 최초 공개 Run 접수에서 main_model_name이 없으면 서비스/Agent의 설정된 기본 모델을 선택한다. Agent 기본값이 없다면 서비스 기본값을 사용하도록 하나의 해석 규칙을 둔다.
- 값이 있으면 등록된 모델 profile에 연결한다. profile이 provider·model·접속 설정을 결정하며 프론트는 endpoint/token을 전달하지 않는다. 알 수 없는 이름은 422로 거절한다. 임의의 이름을 조용히 기본 모델로 바꾸지 않는다.
- 명시적인 요청 모델 선택은 설정 로딩 우선순위(config > env > 기본값)를 변경하는 기능이 아니다. 그 우선순위로 구성된 모델 목록 안에서 요청이 선택한다.
- 선택 모델/profile 버전은 전체 공개 Run에 고정한다. 사용자 HITL 및 Executor 완료 후 resume도 같은 선택을 사용한다. 실행 중 전역 환경변수나 전역 LLM 객체를 덮어써 다른 사용자의 모델까지 바꾸지 않는다.
- 다음 신규 Run은 다른 main_model_name을 선택할 수 있다. 실제 선택한 모델명은 Run 상태에서 확인 가능하게 한다.
- 플랫폼 session_system_prompt 옵션은 제외한다. 이와 별개로 Project.system_prompt는 유지하고 매 Agent 실행에 추가한다. Agent가 관리하는 project_memory도 프로젝트 범위에서 영속 공유하고 프롬프트에 제공한다. Session 자유 settings는 077에서 kernel_profile만 받는 명시적 설정으로 대체했다. Message llm_max_retries는 공개 계약에서 제외한다. [현재 세션 명세](../session-api.md)를 따른다.

## 구현 완료 조건

- 모든 사용자용 HTTP/SSE 요청이 같은 헤더 규칙을 사용한다. 헤더 누락·미등록·비활성 사용자·권한 부족을 일관된 오류로 처리한다.
- 관리자만 사용자 등록/수정/비활성화를 수행한다. /users/me는 조회만 수행하고 토큰을 발급하지 않는다.
- 마지막 관리자 보호, 중복 등록, 사용자와 기본 Project의 원자적 생성, 타 사용자 자원 접근 제한을 검증한다.
- 미종료 Run이 있으면 사용자 삭제를 거절한다. 종료 후 사용자와 하위 데이터를 함께 숨긴다. 숨김과 물리 삭제·외부 실행 취소를 혼동하지 않는다.
- 공개 run_id가 모든 resume와 Executor 대기/완료에서 유지된다. Project system_prompt와 프로젝트 메모리가 실행 프롬프트에 적용되며 메모리의 세션 간 공유·프로젝트 간 격리를 검증한다.
- 모델 기본값/명시 선택/미등록 모델 거절/동시 사용자 간 격리/resume 선택 유지가 동작한다.
- OpenAPI·프론트 명세를 함께 갱신한다. 현재 문서 반영은 실제 API 변경이나 배포 완료가 아니다.
