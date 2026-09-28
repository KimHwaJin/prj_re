# 003 — 사용자 식별·역할·초기 관리자

상태: 구현·격리 검증 완료 / 기존 환경 배포 미수행

브랜치: `feature/refactor-user-identity`

기준: `c2d83e3` (002를 feature/refactor-base로 반영 후 분기)

구현 commit: `c8945e9c27b4102721997eb1abfd59b389a2c24d`

완료일: 2026-09-28

## 문제와 범위

기존 일반 API는 Bearer 내부 UUID로 사용자를 찾고 사용자 등록/수정/삭제는 인증 없이 열려 있었다. role과 사람이 입력하는 공개 ID가 없고, 사용자 삭제 시 미종료 작업을 검사하지 않았다.

이번 작업은 X-User-Id 공통 식별, 공개 문자열 ID와 내부 UUID 연결, admin/user 권한, /users/me, 관리자 전용 사용자 CUD, 최초 관리자 초기화 및 마이그레이션을 구현한다. 마지막 관리자 및 삭제와 작업 접수의 동시성도 검증한다. 비밀번호/로그인/토큰 발급을 추가하지 않는다. 기존 업무 자원의 UUID 소유권 검사는 유지한다.

전체 Run 계약, Message CUD 제거, Project 삭제 정책, Agent 컨텍스트 개선은 후속 작업이다.

## 구현과 검증 결과

| 변경 | 실제 동작 | 주요 파일 |
|---|---|---|
| 공통 식별 | X-User-Id → 활성 사용자/DB role → 내부 Actor/UUID. Bearer 대체. 일반 업무 API는 조회와 공유 잠금을 한 SQL로 수행 | core/auth.py, core/user_identity.py, user_repository.py |
| 공개 ID/role | 기존 UUID FK 유지, public_user_id 및 admin/user 추가. 공개 ID 정규화/유일성/예약어와 role을 DTO·DB에서 검사 | user_model.py, user_schema.py, 20260928_0018_user_identity.py |
| 사용자 관리 API | POST/PATCH/DELETE 관리자 전용. /me 본인 조회. 타 사용자 프로필은 관리자만 조회; 업무 자원 소유권은 기존대로 유지 | routes/users.py, user_service.py |
| 원자성/동시성 | 사용자·기본 프로젝트·membership 동시 생성. 관리 transaction 직렬화 및 대기 후 역할 재검사. 마지막 활성 관리자 보호 | user_service.py |
| 사용자 삭제 | 미종료 Task/Run/LLMRun 검사 후 사용자·프로젝트·세션·메시지 soft delete. 새 작업 접수와 삭제를 사용자 행 잠금으로 순서화 | user_service.py, core/auth.py |
| 초기 관리자 | 배포 CLI로 1회 생성, 같은 관리자 재실행은 멱등. 공개 초기 관리자 API 없음. 기존 일반/삭제 사용자 승격·복구 안 함 | bootstrap_admin.py |

`user_name`은 표시 이름이며 중복을 허용한다. 공개 ID는 소문자 정규화, ASCII 1~100자, 첫 글자 영문/숫자와 나머지 `._@-` 허용, `me` 예약, 삭제 후 재사용 금지로 구현했다. 기존 ID를 바꾸는 API는 추가하지 않았다. 사용자와 기본 프로젝트 연결을 응답의 `default_project_id`로 제공한다. 전체 계약과 실행 명령은 [사용자 API·전환 가이드](../user-identity-api.md)에 정리했다.

## 검증 결과

기존 컨테이너/DB는 변경하지 않고 `postgres:17` 일회성 컨테이너 `dtest-refactor-identity-test`를 새로 만들었다. loopback 임시 포트의 `identity_test` DB만 사용했으며 검증 후 컨테이너를 종료·제거했다. Python은 기존 `.venv`의 3.11.15, 소스는 현재 worktree의 `PYTHONPATH=src`를 사용했다.

- 신규 사용자 테스트 **34개 통과**: 오프라인 계약 14개 + 실제 PostgreSQL 통합 20개.
- 앞 단계 기동·설정 테스트 38개를 함께 실행한 결과 **72개 통과**.
- 전체 회귀(기존 수집 오류 파일 2개 제외): **165 passed / 19 failed**. 002의 결과 XML과 실패 테스트 이름을 비교했으며 추가/변경된 실패 이름은 없다. 기존 실패 19개를 성공으로 보고하지 않는다.
- `git diff --cached --check` 통과.

실제 PostgreSQL에서 확인한 항목:

1. 이전 revision 0017 상태에 사용자·프로젝트를 넣고 0018로 upgrade: 공개 ID/role backfill 및 내부 UUID/FK 보존. downgrade → 재-upgrade도 확인.
2. 관리자 초기화 재실행 및 동시 초기화가 사용자/기본 프로젝트를 각각 하나만 생성. 실제 `python -m bootstrap_admin` 명령도 동일 설정으로 두 번 실행해 멱등성 확인.
3. 헤더 누락·잘못된 ID·미등록·Bearer-only 401, 일반 사용자의 관리 요청 403, 타 사용자 조회/업무 자원 접근 제한, 관리자에 의한 role 변경.
4. 중복 ID 동시 등록은 201/409로 수렴. 기본 프로젝트 생성 실패를 DB constraint로 주입했을 때 사용자 생성도 rollback.
5. 관리자 두 명의 동시 자기 강등은 200/409이며 활성 관리자 1명 유지. 마지막 관리자 삭제 409. 이미 읽은 Actor가 관리자였어도 실제 처리 전에 강등되면 사용자 등록 403.
6. pending/running/waiting_input Task 및 Task 없는 pending/running/interrupted Run이 있으면 사용자 삭제 409. 완료 Task의 과거 interrupted 구간은 삭제를 막지 않음. 성공한 삭제는 사용자·프로젝트·세션·메시지에 모두 적용되고 이후 조회 401 및 ID 재등록 409.
7. 이미 사용자 공유 잠금을 가진 접수 transaction이 새 pending Task를 commit하면 뒤따른 삭제는 409. 삭제가 먼저 사용자 행을 비활성화하면 뒤따른 접수는 401. 두 방향 모두 실제 DB lock 대기를 확인.
8. X-User-Id로 실제 Run 접수 API 호출 시 저장된 requested_by_user_id는 기존 내부 UUID. 완료 상태를 테스트에서 지정한 뒤 실제 SSE 경로가 같은 헤더로 이벤트를 반환하고 타 사용자 접근을 거절. LLM/Graph/Executor 실행은 하지 않음.

```sh
PYTHONPATH=src python -m pytest \
  src/app/test/test_user_identity.py \
  src/app/test/test_user_identity_postgres.py \
  src/app/test/test_bootstrap_settings.py -q
```

PostgreSQL 테스트에는 전용 DB URL 환경변수 `DTEST_IDENTITY_TEST_DATABASE_URL`이 필요하다. 이 테스트는 명시된 localhost `identity_test` DB의 public 스키마를 초기화하므로 일반 애플리케이션 DB에 사용하지 않는다. 환경변수가 없으면 PostgreSQL 테스트는 skip이며 실제 DB 검증 성공으로 계산하지 않는다.

전체 회귀 명령은 위 환경변수와 함께 `python -m pytest src/app/test -q --tb=no --ignore=src/app/test/test_select_features.py --ignore=src/app/test/test_split_dataset.py`다. 제외 사유와 이전 실패 목록의 범주는 [002 기록](002-bootstrap-configuration.md)을 따른다.

## 배포/호환성과 후속 범위

- 새 API는 Bearer 및 이름으로 사용자 찾기 API와 호환되지 않는다. 기존 데모/Locust의 자동 등록·Bearer 흐름도 아직 이관하지 않았다. README 상단에 현재 계약과 이전 실행 가이드를 구분해 기록했다.
- 기존 사용자에게 자동으로 admin을 부여하지 않는다. 새 ID로 최초 관리자 CLI를 실행한 다음 관리자 API로 역할을 관리한다.
- 마이그레이션과 구 버전 사용자 쓰기 API의 무중단 혼용은 검증/지원하지 않는다. 전환 시 사용자 쓰기를 중지/분리하고 마이그레이션 → 관리자 초기화 → 새 API/클라이언트로 전환한다. downgrade는 새 공개 ID/role 정보를 제거한다.
- 실제 운영/기존 로컬 DB 마이그레이션, 기존 컨테이너 재배포, 원격 push는 하지 않았다. 이번 파생 브랜치도 아직 기준 브랜치에 통합하지 않았다.
- 기본 READ COMMITTED transaction과 공통 dependency를 사용하는 사용자 API 기준으로 동시성을 검증했다. DB에 직접 쓰는 임의 스크립트까지 서비스 권한/마지막 관리자 규칙을 강제하는 것은 아니다.
- 대기/실행 상태는 현재 Task·AgentRun 모델을 기준으로 검사한다. 별도 WAITING_EXECUTOR 상태, 공개 run_id 통합, 장기 Executor 복구, 취소 정체(001) 및 Worker 동시성은 다음 실행기 단계다.
- 일반 사용자 역할 확인/소유권 처리는 유지했고 관리자 역할이 대화 전체 접근을 허용하지 않는다. Message CUD 제거, Project 삭제/이동 제한, Agent 프로젝트 컨텍스트는 미수행이다.
