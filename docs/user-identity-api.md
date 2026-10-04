# 사용자 식별·관리 API — 구현 계약

## 호출 방법

2026-10-01부터 사용자 식별은 사내 SSO 로그인 후 발급된 HttpOnly 쿠키로 통일한다. `X-User-Id`나 Bearer만으로는 인증되지 않는다. 자세한 설정·폐쇄망 SDK 연결·Swagger 사용법은 [SSO 안내](sso-authentication.md)를 따른다. 내부 SDK는 공개할 필요 없이 `integrations/company_sso.py`의 두 함수만 사내에서 연결한다. 미연결 상태의 로그인은 503이다.

1. 브라우저에서 `GET /api/v1/auth/login/sso`로 로그인한다.
2. `GET /api/v1/users/me`에서 본인·역할·`csrf_token`·`login_expires_at`을 읽는다. 이 API는 토큰 발급/사용자 등록 API가 아니다.
3. GET은 쿠키, POST/PATCH/PUT/DELETE는 쿠키와 `X-CSRF-Token`을 함께 보낸다.
4. `POST /api/v1/auth/logout`은 해당 로그인 세션을 폐기한다. 실행 중인 Agent/Executor를 취소하지 않는다.

```http
POST /api/v1/users
Cookie: dtest_session=<browser-managed-session>
X-CSRF-Token: <users/me response>
Content-Type: application/json

{"user_id":"user-001","user_name":"홍길동","role":"user"}
```

SSO 최초 성공 시 검증된 사번을 공개 ID로 사용해 일반 사용자·기본 프로젝트·owner membership을 같은 transaction에서 생성한다. 기존 활성 사용자는 내부 UUID·역할·이름·기본 프로젝트를 유지하며 비활성 사용자는 자동 복구하지 않는다. 신규 자동 등록은 설정으로 차단할 수 있다. 기존 공개 ID가 사번과 다르면 자동 매핑되지 않으므로 배포 전에 명시적으로 이관해야 한다.

`/me` 응답의 `user_id`는 공개 문자열이며 내부 UUID를 인증 토큰으로 노출하지 않는다. `default_project_id`는 Project UUID다. 기존 데이터에 기본 프로젝트가 없으면 조회 응답은 null이며 조회 중 자동 생성하지 않는다. `csrf_token`은 이 로그인 세션의 변경 요청용 값이고 `login_expires_at`은 Unix 초다. 응답은 `Cache-Control: no-store`다.

공개 ID는 1~100자의 영문·숫자와 `._@-`를 허용하고 첫 글자는 영문·숫자다. 앞뒤 공백 제거 후 소문자로 정규화하며 `me`는 고정 경로로 예약한다. `user_name`은 표시 이름이고 중복 가능하다. 공개 ID는 변경할 수 없으며 soft delete 후에도 재사용하지 않는다.

## 권한과 오류

| 경로 | 권한/결과 |
|---|---|
| GET /users | 관리자만 사용자 요약 목록. 검색·권한·활성/삭제 필터와 cursor 페이지 |
| POST /users | 관리자. role=admin/user, 생략하면 user. 성공 201 + Location |
| GET /users/me | 활성 사용자 자신의 정보·CSRF·로그인 만료 시각. 자동 가입·토큰 발급 없음 |
| GET /users/{user_id} | 활성 본인 또는 관리자. 관리자는 삭제된 사용자도 조회. 일반 사용자의 타 사용자 조회는 404 |
| PATCH /users/{user_id} | 관리자. user_name/role만 허용; 빈 patch/null/ID 변경은 422 |
| DELETE /users/{user_id} | 관리자. 미종료 작업 및 마지막 관리자 검사 후 soft delete; 성공 204 |

로그인 쿠키 누락·만료·비활성 사용자는 401, 변경 요청 CSRF 불일치·권한 부족은 403, 대상 부재는 404, ID 중복·마지막 관리자·미종료 작업 충돌은 409다. Redis/SDK 장애는 503이며 세션이 없다고 위장하지 않는다. Swagger는 SSO 로그인 버튼으로 로그인하고 쿠키/CSRF를 자동 전송한다. GET SSE에는 쿠키를, POST stream에는 쿠키와 CSRF를 보낸다. DB transaction은 stream 시작 전에 끝낸다.

관리자는 사용자 프로필을 관리할 수 있지만 다른 사용자의 프로젝트·세션·대화를 자동으로 볼 수는 없다. 기존 업무 API의 내부 UUID 소유권 검사는 유지한다. 호출자 역할은 등록/수정 body의 role 값이 아니라 기존 DB 역할로 판단한다.

`/users/by-name/{user_name}`은 제거했다. 표시 이름 기반 로그인·UUID 취득 흐름을 `/users/me`로 대체한다. 데모는 쿠키/CSRF로 전환했다. 기존 Locust·진단 스크립트의 헤더 인증은 이관하지 않았으며 운영용 우회 인증은 제공하지 않는다.

## 관리자 목록과 삭제 사용자 상세

```http
GET /api/v1/users?q=employee&role=user&status=active&limit=50&sort=-created_at
Cookie: dtest_session=<browser-managed-session>
```

관리자 쿠키로 호출하며 GET에는 CSRF 헤더가 필요 없다. 로그인되지 않았거나 삭제된 호출자는 401, 일반 사용자는 403이다. 정상 응답은 `Page[UserSummary]`이며 `Cache-Control: no-store`를 적용한다. 결과가 없으면 `items: []`, `page.has_next: false`, `page.next_cursor: null`이다.

| Query | 의미·기본값 |
|---|---|
| q | 공개 user_id 또는 표시 이름의 부분 검색. 대소문자를 무시하고 앞뒤 공백 제거. 지정하면 1~100자이며 공백만 있으면 422. `%`, `_`, `\`는 wildcard가 아니라 문자 그대로 찾음 |
| role | admin/user 정확 일치. 생략하면 둘 다 |
| status | active(기본)=활성, deleted=soft delete만, all=둘 다. 계정 상태이며 Run/로그인 세션 상태와 별개 |
| limit | 기본50·최소1·최대200 |
| sort | -created_at(기본) 또는 created_at. 동시각은 내부 UUID로 순서를 확정 |
| cursor | 응답의 next_cursor를 그대로 전송. 페이지 간 q/role/status/sort/날짜 조건을 유지. 잘못된 형식은 400 |
| created_at_from | 등록 시각의 포함 하한. ISO8601, 시간대 포함 권장 |
| created_at_to | 등록 시각의 제외 상한. from≥to이면 422 |

예제는 [user_list.json](contracts/agent-api/responses/user_list.json) · [필드 주석](contracts/agent-api/responses/user_list.jsonc)을 참고한다.

| items 필드 | 의미 |
|---|---|
| user_id | 공개 문자열 ID. 내부 DB UUID를 사용자 식별값으로 반환하지 않음 |
| user_name | 표시 이름. 중복 가능 |
| role | admin 또는 user |
| is_active | soft delete되지 않은 계정인지. 로그인 중인지 또는 쿠키가 유효한지를 뜻하지 않음 |
| created_at | 최초 등록 시각 |
| updated_at | 사용자 레코드 최종 변경 시각 |
| deleted_at | soft delete 시각. 활성 사용자는 null |
| page.has_next | 다음 페이지 존재 여부 |
| page.next_cursor | 다음 페이지 요청값. 마지막 페이지는 null |

목록은 사용자 요약만 읽고 기본 프로젝트·CSRF·로그인 만료값은 포함하지 않는다. 인증1회와 사용자 페이지1회의 **2 SELECT**이며 사용자별 프로젝트 추가 조회나 전체 COUNT를 하지 않는다. `limit+1` 행까지만 읽어 다음 페이지를 판단한다. cursor 내부에는 정렬에 필요한 생성 시각·DB UUID가 인코딩되어 있으며 암호화·서명된 인증 토큰이 아니다. 별도 필터 고정이나 전체 목록 snapshot을 보장하지 않으므로 조회 중 추가/삭제/권한 변경이 있으면 후속 페이지의 결과가 달라질 수 있다. 사용자 이름/ID 부분 검색의 대규모 데이터 성능은 별도 측정 대상이며 이번에 새 검색 인덱스나 속도 개선 수치를 추가하지 않았다.

`GET /api/v1/users/{user_id}`의 기존 경로와 `UserRead` 필드는 유지한다. 관리자는 별도 query 없이 삭제된 사용자도 읽으며 `delete_yn: Y`, `deleted_at`, `default_project_id: null`을 받는다(기본 프로젝트도 삭제된 경우). 일반 사용자는 활성 본인만 읽고 다른 사용자는 404다. 삭제된 사용자는 자신의 과거 쿠키로도 401이다. 상세 응답에도 `Cache-Control: no-store`를 적용한다.

[삭제 사용자 예제](contracts/agent-api/responses/deleted_user.json) · [필드 주석](contracts/agent-api/responses/deleted_user.jsonc). 상세의 `default_project_id`는 활성 기본 프로젝트만 가리키고 없으면 null이다. `delete_yn`은 목록의 `is_active`와 같은 삭제 정보를 기존 상세 형식으로 표현한다. 그 외 user_id/user_name/role/created_at/updated_at/deleted_at의 의미는 목록과 같다. `/users/me`만 CSRF·로그인 만료를 추가한다.

조회는 사용자·프로젝트를 생성하거나 복원하지 않는다. 삭제된 사용자 PATCH/DELETE는 404, 같은 공개 ID로 재등록은 409, SSO 재로그인은 403을 유지한다. 복구·재활성화 API는 추가하지 않았다. 관리자 목록 권한이 타 사용자의 프로젝트·세션·대화 조회 권한을 자동으로 부여하지 않는다.

## 초기 관리자와 DB 전환

revision `20260928_0018`이 `users.public_user_id`, `users.role`과 제약조건을 추가한다. 기존 UUID PK와 FK를 변경하지 않는다. 기존 사용자는 `public_user_id = 기존 UUID의 소문자 문자열`, `role=user`로 이관한다. 기존 이름에서 ID를 추정하거나 특정 사용자를 자동 관리자로 승격하지 않는다.

선택 YAML/환경변수에 실제 DB 설정을 먼저 넣고 같은 설정으로 두 명령을 실행한다:

```sh
export APP_ENV=dev
export SERVICE_CONFIG_FILE=/mounted/service-config.yml
PYTHONPATH=src python -m alembic -c alembic.crud.ini upgrade head
PYTHONPATH=src python -m bootstrap_admin --user-id admin-001 --user-name 관리자
```

초기화 명령은 공개 API가 아니다. 첫 관리자와 기본 프로젝트를 만들고, 같은 활성 관리자 ID로 재실행하면 기존 결과를 그대로 반환한다. 이름을 재지정해도 덮어쓰지 않는다. 이미 다른 관리자가 있으면 새 관리자는 관리자 API로 등록한다. 기존 일반 사용자나 삭제된 사용자 ID를 자동 승격/복구하지 않는다. 초기화 ID는 실제 SSO 사번을 사용하고 첫 일반 사용자 자동 가입 전에 초기화한다. 이미 일반 사용자로 가입된 ID는 이 명령으로 승격되지 않으므로 기존 관리자의 PATCH 또는 승인된 DB 이관이 필요하다.

전환은 API 호환성을 깨는 변경이다. 기존 버전과 새 버전을 섞어서 사용자 쓰기를 계속하는 rolling 호환성을 제공하지 않는다. 사용자 쓰기 트래픽을 중지/이전 버전에서 분리하고, 마이그레이션 → 관리자 초기화 → 새 API 및 헤더 클라이언트 배포 순서로 전환한다. downgrade는 새 공개 ID와 role 컬럼을 제거하므로 이 정보를 잃는다. 테스트에서 downgrade/upgrade를 검증한 것은 운영 rollback의 무손실 보장을 뜻하지 않는다.

## 동시성과 삭제

사용자 관리/초기화 transaction은 PostgreSQL advisory lock 하나로 직렬화한다. 관리 요청은 lock을 얻은 다음 호출자의 현재 역할을 다시 조회한다. 두 관리자의 동시 강등/삭제로 활성 관리자 수가 0이 되는 것을 막는다. 이 전역 lock은 일반 대화 요청이나 Agent 실행을 직렬화하지 않는다.

일반 업무 API는 사용자 조회 한 번에 공유 행 잠금(`FOR SHARE`)도 얻는다. 동일 사용자의 일반 요청끼리는 공유 가능하다. 삭제는 대상 사용자에 배타 잠금을 얻어 먼저 접수된 transaction이 끝나기를 기다린 뒤 미종료 작업을 검사한다. 삭제가 먼저 완료되면 대기 중이던 새 요청은 비활성 사용자를 보고 401로 종료한다. 잠금은 DB transaction 동안만 유지되며 SSE 인증 transaction은 stream 시작 전에 종료한다. 현재 SQLAlchemy/PostgreSQL의 기본 READ COMMITTED transaction을 전제로 검증했다.

현재 Task의 pending/running/waiting_input 및 pending/running AgentRun, Task 없는 interrupted Run, queued/running LLMRun이 남아 있으면 삭제를 거절한다. 공개 Run은 waiting_executor를 구분하지만 내부 Task의 waiting_input에는 Executor 대기도 포함된다. 공개 상태 이름만으로 삭제를 허용하지 않고 실제 미종료 작업을 검사한다. 완료 Task에 속한 이전 interrupted 구간은 역사 기록이므로 삭제를 막지 않는다.

삭제 성공 시 사용자 및 소유 프로젝트·세션·메시지를 한 transaction에서 숨긴다. Task/Run/체크포인트를 물리 삭제하거나 외부 Executor를 취소하지 않는다. 이후 쿠키 인증의 DB 활성 사용자 검사와 일반 소유권 조회에서 접근이 차단된다. 세션 프로젝트 이동은 지원하지 않는다. 세션/프로젝트 삭제 보호는 [현재 CRUD 정책](crud-lifecycle-policy.md), 장기 Executor 상태 전이 개선은 후속 목록을 따른다.

## 검증 실행

```sh
PYTHONPATH=src python -m pytest src/api_service/test/test_user_identity.py -q
# 전용 일회성 localhost DB identity_test에서만 실행. public 스키마를 초기화한다.
DTEST_IDENTITY_TEST_DATABASE_URL=postgresql+asyncpg://tester:password@127.0.0.1:TEST_PORT/identity_test \
  PYTHONPATH=src python -m pytest src/api_service/test/test_user_identity_postgres.py src/api_service/test/test_admin_user_reads_postgres.py src/api_service/test/test_sso_auth_postgres.py -q
```

기존 역할/소유권 구현 이력은 [003 기록](improvements/003-user-identity.md), 현재 SSO 검증은 [045 기록](improvements/045-sso-authentication.md)을 참고한다. 기존 업무 테스트의 신원 double은 테스트 패키지에만 존재하며 production wheel에는 포함하지 않는다.
