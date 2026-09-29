# 사용자 식별·관리 API — 구현 계약

## 호출 방법

모든 사용자용 `/api/v1` API는 `X-User-Id` 헤더로 호출자를 식별한다. 비밀번호, 로그인, 토큰 발급은 없다. 전달된 등록 사용자 ID를 신원으로 신뢰하고 DB의 활성 여부·역할·자원 소유권을 검사한다. 내부 Worker는 기존 내부 UUID를 그대로 사용한다. `/health`, `/service/ready`에는 사용자 헤더를 요구하지 않는다.

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

`/me` 응답은 `user_id`, `user_name`, `role`, `default_project_id`, 삭제 여부와 생성/변경 시각이다. `user_id`는 공개 문자열이고 내부 UUID를 토큰처럼 돌려주지 않는다. `default_project_id`는 기존 Project UUID다. 등록 시 기본 프로젝트와 owner membership이 사용자와 같은 transaction에서 만들어진다. 기존 데이터에 기본 프로젝트가 없으면 조회 응답은 null이며 조회 중 자동 생성하지 않는다.

공개 ID는 1~100자의 영문·숫자와 `._@-`를 허용하고 첫 글자는 영문·숫자다. 앞뒤 공백 제거 후 소문자로 정규화하며 `me`는 고정 경로로 예약한다. `user_name`은 표시 이름이고 중복 가능하다. 공개 ID는 변경할 수 없으며 soft delete 후에도 재사용하지 않는다.

## 권한과 오류

| 경로 | 권한/결과 |
|---|---|
| POST /users | 관리자. role=admin/user, 생략하면 user. 성공 201 + Location |
| GET /users/me | 활성 사용자 자신의 정보. 자동 가입·토큰 발급 없음 |
| GET /users/{user_id} | 본인 또는 관리자. 일반 사용자의 타 사용자 조회는 404 |
| PATCH /users/{user_id} | 관리자. user_name/role만 허용; 빈 patch/null/ID 변경은 422 |
| DELETE /users/{user_id} | 관리자. 미종료 작업 및 마지막 관리자 검사 후 soft delete; 성공 204 |

헤더 누락·미등록·비활성·형식 오류는 401, 관리 권한 부족은 403, 대상 부재는 404, ID 중복·마지막 관리자·미종료 작업 충돌은 409다. Bearer 헤더만 보내면 401이다. Swagger Authorize도 `X-User-Id`를 사용한다. SSE는 헤더를 지원하는 fetch streaming 클라이언트로 호출한다.

관리자는 사용자 프로필을 관리할 수 있지만 다른 사용자의 프로젝트·세션·대화를 자동으로 볼 수는 없다. 기존 업무 API의 내부 UUID 소유권 검사는 유지한다. 호출자 역할은 등록/수정 body의 role 값이 아니라 기존 DB 역할로 판단한다.

`/users/by-name/{user_name}`은 제거했다. 표시 이름 기반 로그인·UUID 취득 흐름을 `/users/me`로 대체한다. 기존 데모/Locust의 Bearer 및 무인 가입 흐름은 아직 이관하지 않았으며 후속 Run/CRUD 계약 정리 때 함께 변경한다.

## 초기 관리자와 DB 전환

revision `20260928_0018`이 `users.public_user_id`, `users.role`과 제약조건을 추가한다. 기존 UUID PK와 FK를 변경하지 않는다. 기존 사용자는 `public_user_id = 기존 UUID의 소문자 문자열`, `role=user`로 이관한다. 기존 이름에서 ID를 추정하거나 특정 사용자를 자동 관리자로 승격하지 않는다.

선택 YAML/환경변수에 실제 DB 설정을 먼저 넣고 같은 설정으로 두 명령을 실행한다:

```sh
export APP_ENV=dev
export SERVICE_CONFIG_FILE=/mounted/service-config.yml
PYTHONPATH=src python -m alembic -c alembic.crud.ini upgrade head
PYTHONPATH=src python -m bootstrap_admin --user-id admin-001 --user-name 관리자
```

초기화 명령은 공개 API가 아니다. 첫 관리자와 기본 프로젝트를 만들고, 같은 활성 관리자 ID로 재실행하면 기존 결과를 그대로 반환한다. 이름을 재지정해도 덮어쓰지 않는다. 이미 다른 관리자가 있으면 새 관리자는 관리자 API로 등록한다. 기존 일반 사용자나 삭제된 사용자 ID를 자동 승격/복구하지 않는다. 초기화 ID는 새로 선택한다.

전환은 API 호환성을 깨는 변경이다. 기존 버전과 새 버전을 섞어서 사용자 쓰기를 계속하는 rolling 호환성을 제공하지 않는다. 사용자 쓰기 트래픽을 중지/이전 버전에서 분리하고, 마이그레이션 → 관리자 초기화 → 새 API 및 헤더 클라이언트 배포 순서로 전환한다. downgrade는 새 공개 ID와 role 컬럼을 제거하므로 이 정보를 잃는다. 테스트에서 downgrade/upgrade를 검증한 것은 운영 rollback의 무손실 보장을 뜻하지 않는다.

## 동시성과 삭제

사용자 관리/초기화 transaction은 PostgreSQL advisory lock 하나로 직렬화한다. 관리 요청은 lock을 얻은 다음 호출자의 현재 역할을 다시 조회한다. 두 관리자의 동시 강등/삭제로 활성 관리자 수가 0이 되는 것을 막는다. 이 전역 lock은 일반 대화 요청이나 Agent 실행을 직렬화하지 않는다.

일반 업무 API는 사용자 조회 한 번에 공유 행 잠금(`FOR SHARE`)도 얻는다. 동일 사용자의 일반 요청끼리는 공유 가능하다. 삭제는 대상 사용자에 배타 잠금을 얻어 먼저 접수된 transaction이 끝나기를 기다린 뒤 미종료 작업을 검사한다. 삭제가 먼저 완료되면 대기 중이던 새 요청은 비활성 사용자를 보고 401로 종료한다. 잠금은 DB transaction 동안만 유지되며 SSE 인증 transaction은 stream 시작 전에 종료한다. 현재 SQLAlchemy/PostgreSQL의 기본 READ COMMITTED transaction을 전제로 검증했다.

현재 Task의 pending/running/waiting_input 및 pending/running AgentRun, Task 없는 interrupted Run, queued/running LLMRun이 남아 있으면 삭제를 거절한다. WAITING_EXECUTOR 별도 상태는 아직 없으며 현재의 대기 상태를 기준으로 검사한다. 완료 Task에 속한 이전 interrupted 구간은 역사 기록이므로 삭제를 막지 않는다.

삭제 성공 시 사용자 및 소유 프로젝트·세션·메시지를 한 transaction에서 숨긴다. Task/Run/체크포인트를 물리 삭제하거나 외부 Executor를 취소하지 않는다. 이후 헤더 인증과 일반 소유권 조회에서 접근이 차단된다. 세션 이동·프로젝트 삭제 정책 자체와 장기 Executor 상태 전이 개선은 후속 단계다.

## 검증 실행

```sh
PYTHONPATH=src python -m pytest src/api_service/test/test_user_identity.py -q
# 전용 일회성 localhost DB identity_test에서만 실행. public 스키마를 초기화한다.
DTEST_IDENTITY_TEST_DATABASE_URL=postgresql+asyncpg://tester:password@127.0.0.1:TEST_PORT/identity_test \
  PYTHONPATH=src python -m pytest src/api_service/test/test_user_identity_postgres.py -q
```

실제 테스트 결과와 남은 범위는 [003 기록](improvements/003-user-identity.md)을 참고한다.
