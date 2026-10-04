# 082 — 관리자 사용자 요약 목록·삭제 사용자 상세

- 날짜: 2026-10-04
- 브랜치: feature/admin-user-read-contract
- 기준: feature/run-diagnostics-contract / 0c5e838 (081)
- 상태: 구현·검증 완료, 베이스 미병합·미푸시·미배포

## 문제와 승인된 방향

관리 화면을 제공하려고 하지만 사용자 등록/단건 조회/수정/삭제만 있고 관리자 사용자 목록이 없었다. 삭제된 계정의 단건 정보도 관리자에게 404여서 삭제 상태를 확인할 수 없었다. 사용자 승인에 따라 관리자 요약 목록과 삭제 사용자 상세 읽기를 추가한다. SSO 최초 로그인은 일반 사용자·기본 프로젝트 자동 생성, 기존 삭제 사용자는 자동 복구하지 않는 정책을 유지한다.

## API 계약

- `GET /api/v1/users`: 관리자 전용 `Page[UserSummary]`. q는 공개 ID·표시 이름의 대소문자 무시 부분 검색이며 앞뒤 공백을 제거하고 %, _, 역슬래시를 문자 그대로 찾는다. 지정하면1~100자·공백만은422. role은admin/user 정확 일치, status는active(기본)/deleted/all이다.
- 공통 페이지: 기본50·최대200, created_at 양방향·UUID tie-break, cursor, 생성 시각[from,to). 잘못된 enum/limit/sort/날짜는422, 잘못된 cursor는400. 다음 페이지에서도 동일 검색·필터·정렬을 유지한다.
- 요약7필드: 공개 문자열 user_id, user_name, role, is_active, created_at, updated_at, deleted_at. is_active는 soft delete 여부이지 로그인/쿠키 상태가 아니다. default_project_id·CSRF·로그인 만료값은 포함하지 않는다.
- 기존 `GET /api/v1/users/{user_id}`와 UserRead 필드는 유지한다. 관리자는 삭제된 사용자도 읽고 일반 사용자는 활성 본인만 조회한다. 타 사용자 조회는404다. UserRead의 delete_yn Y/N을 다른 API 변경 없이 유지한다.
- 삭제 계정에 대한 수정/중복 삭제404, 공개 ID 재등록409, SSO bind403을 유지한다. 조회에서 사용자·Project를 생성/복구하거나 타 사용자 업무 자원 소유권을 우회하지 않는다.
- 목록·상세 정상 응답은Cache-Control:no-store. 활성 로그인 쿠키가 필요하며 GET에는CSRF가 필요 없다. X-User-Id/Bearer만으로 인증되지 않는다.

## 구현과 조회 비용

- services/user_queries.py는 User 필드8개(내부 cursor UUID+공개 요약7개)의 SQL Bundle 투영을 사용한다. 데이터/권한/ORM 객체를 캐시하지 않고 SQL 구조만 재사용한다. Project를 조회하거나 목록마다 기본 프로젝트를 구하지 않는다.
- 조회당 인증1+페이지1의2 SELECT, 최대limit+1행이다. 전체COUNT나N+1이 없다. 사용자 수가 늘 때 목록 크기·쿼리 수를 제한하는 구현이며 처리량/시간 개선 측정은 아니다. 이름/ID 부분 검색의 대규모DB 실행 비용은 별도 검증 대상이다.
- UserService.read만 관리자에 대해 active_only를 해제한다. 쓰기 경로의 _target 기본은 활성만이며 SSO/provision/마지막 관리자 보호·미종료 작업 삭제 보호는 변경하지 않았다.
- 상세 기본 프로젝트는 활성 Project만 읽고 없으면null이며 조회로 새로 생성하지 않는다. User/Project 테이블·migration·환경변수·의존성은 추가하지 않았다.
- cursor에는 생성 시각·내부 UUID가 인코딩된다. 공개 user_id 필드는 내부 UUID가 아니지만 cursor를 암호화/서명 토큰으로 설명하지 않는다. 페이지 간 데이터 snapshot·필터 고정·총 개수는 보장하지 않는다.

## 검증

격리 PostgreSQL17 컨테이너dtest-user-read-test-20261004의 localhost49523/identity_test에서 수행했다. 기존 서비스DB/Redis/Executor/실제LLM·사내SDK는 호출하지 않았다. 목록/조회는 HTTP identity double, 별도 SSO 회귀는 실제 cookie·CSRF 인증 경로와 SDK/Redis double을 사용했다.

- 사용자 목록10개·기존 사용자 관리/SSO+새 cookie 사례: **37 passed, 1 skipped**. 제외된1개는 별도의 Agent 통합 DB/설정 파일이 필요한 기존 Run/resume E2E이며 SSO SDK를 실연결한 검증은 아니다.
- 페이지1/7/200×양방향·동시각 tie-break·205건 분할·기본50·중복/누락·정확한7필드·2 SELECT·8열·limit+1상한·Project/COUNT 부재를 확인했다.
- q/role/status9조합, 영문 대소문자·한글·공백·literal %, _, 역슬래시·빈 목록·날짜 포함/제외·잘못된 입력을 검증했다.
- 관리자/일반/삭제 호출자의401/403/404, /me 경로, 실제 SSO cookie 로그인→관리자 목록→CSRF 삭제→삭제 목록/상세, 헤더/Bearer 우회 불가를 확인했다.
- 삭제 상세 조회 전후 User ID/권한/삭제시각/갱신시각, Project 삭제 상태·User/Project 수가 같고, 수정·재등록·SSO bind로 자동 복원되지 않음을 확인했다. 기존 last-admin·동시 관리·삭제 보호 회귀도 통과했다.
- 전체src: **681 passed, 466 skipped, 74 warnings**. opt-in PG는 별도 위37개이며 합산하지 않는다. 추가된11개 PG 사례가 일반 실행에서는skip이다. 기존 no-checkpointer durability 경고는 유지했다.
- 클린staging wheel/isolatedPython 검증 통과: checkout 미참조, 관리자 사용자 요약/필터/schema,34paths·5개Agent 역할·리소스·조립을 확인했다. GET을 기존 `/users` POST와 같은 path에 추가하므로 전체 path 수는34로 유지된다. 첫 smoke의 parameter 비교에는 공통 CSRF header도 포함되어 실패했고 query만 비교하도록 검증을 바로잡았다. API 인증 동작은 바꾸지 않았다.
- scoped OpenAPI16paths/39models, UserSummary/UserRead serialization schema,2개 JSON/JSONC 응답 예제와 전체 필드 주석을 갱신했다. **30files·7,055주석·6inline blocks**, JSON/JSONC 동등성·검증 규칙 보존을 확인했다.
- git diff --check 통과. 이번 임시 DB를 종료하고 서비스 컨테이너·원본 checkout은 수정하지 않았다. 실제 배포·SDK 연동·처리량 A/B는 미실행이다.

## 문서와 후속

[사용자 API](../user-identity-api.md), [기계 판독 계약·예제](../contracts/agent-api/README.md), [필드 주석](../contracts/field-comments.md)을 갱신했다. 관리 화면은 목록의 공개 user_id로 기존 상세 경로를 호출하고 status=deleted/all로 삭제 상태를 조회한다. 복구API·orphan Task 탐색·Message CUD·Workflow CRUD·모델 호출 수·광범위 운영 개선의 기존 보류는 유지한다.
