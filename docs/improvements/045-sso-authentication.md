# 045. 사내 SSO 로그인·Redis 세션·API 인증 통합

> 후속 통합 기록 (2026-10-01): 이 작업은 038~046과 함께 `feature/refactor-base`에 fast-forward 반영되고 `origin=KimHwaJin/prj_re`에 게시되었다. 파생 브랜치도 보존·게시했으며 원격 SHA 일치를 확인했다. 아래의 미병합·push 미수행 문구는 당시 완료 시점 기록이다. 배포·후속 기능 상태는 그대로다. [통합·검증 기록](046-api-workflow-reference.md).

| 항목 | 내용 |
|---|---|
| 상태 | 서비스 구현·881개 회귀·패키지 검증 완료 / 사내 SDK 연결·실제 SSO 검증 미완료 |
| 시작일 / 서비스 구현 완료일 | 2026-10-01 / 2026-10-01 |
| 브랜치 | feature/sso-auth |
| 기준 commit | 8b89dc75407fb10cbace5b764376d2eb0688f842 — 044의 코드·문서에서 분기 |
| 구현 commit | 07e0397aa45dc9e43e7716a934683b3649a2d545 |

## 문제와 사용자 결정

기존 `X-User-Id`는 전달된 ID를 신뢰하는 개발용 식별 방식이었다. 사내 SSO를 적용하고 다른 서비스에도 같은 방식으로 사용할 수 있는 인증 모듈이 필요하다. 내부 SDK는 회사 밖으로 공유할 수 없으므로 소스·설치정보를 요구하거나 실제 메서드를 추측하지 않는다. 최초 검증 직원은 일반 사용자와 기본 프로젝트를 자동 등록한다. 관리자 권한은 기존 DB 정책을 유지하고 비활성 사용자는 복구하지 않는다.

## 변경

- `service_auth.sso`: SDK port, 동기 SDK thread offload, 랜덤 HttpOnly 로그인 쿠키, 해시 SID Redis key, 고정 TTL, CSRF 검증, 로그인·로그아웃 router와 공통 installer. API·Agent 구현 import 없이 재사용한다.
- `integrations.company_sso`: 폐쇄망에서 구현할 `verify_employee`, `build_login_url` 두 함수. 공식 SDK를 연결하지 않으면 로그인은 503이다. 공개 인증 우회나 가짜 운영 로그인 API는 없다.
- 기존 `get_current_actor/get_current_user_id/get_stream_user_id`의 신원 입력을 쿠키 세션으로 교체한다. DB 활성 사용자·최신 role·소유권·FOR SHARE 접수 잠금·SSE의 짧은 인증 transaction을 유지한다.
- SSO 사번이 기존 `public_user_id`와 일치하면 내부 UUID를 보존한다. 신규 User/기본 Project/OWNER membership은 한 transaction에서 생성한다. 일치하지 않는 이전 계정은 자동 연결하지 않는다.
- `GET /auth/login/sso`, `POST /auth/logout`; `/users/me`에는 CSRF와 Unix 만료 초를 추가한다. 공개 Run 경로·body·멱등 키는 유지한다. 변경 요청은 쿠키와 `X-CSRF-Token`이 필요하다.
- 중앙 설정에 auth 그룹을 추가한다. 선택 config > env > 기본값, 환경별 Redis namespace, cookie/TTL/auto_register/redirect/pool/timeout 설정을 제공한다. 환경변수 별도 재로딩은 없다.
- 같은 Redis 서버·DB를 쓰되 Streams의 BLOCK 연결풀과 로그인 연결풀은 분리한다. 로그인은 별도 string key와 TTL을 사용하며 Stream/group/ACK/보존 설정을 변경하지 않는다.
- 서비스 Swagger는 SSO 로그인·로그인 상태 표시·동일 API 대상의 쿠키/CSRF 자동 전송을 제공한다. 플랫폼 생성 app에는 `/service/docs`, 로컬 app에는 `/docs`를 붙인다. Gaia core는 수정하지 않는다. Demo도 동일 쿠키 방식을 사용한다.

설정 예제·사내 연결 순서·다른 서비스 재사용은 [SSO 안내](../sso-authentication.md), 현재 사용자 관리 명세는 [사용자 API](../user-identity-api.md)를 따른다.

## 검증

- SSO 단위 36개: SID hash/PII 제외, 고정 TTL, 세션 회전·폐기, Cookie flags, CSRF, DB 최신 role/비활성, redirects, SDK/Redis 장애·deadline, 설정 우선순위, Swagger 보안 메타데이터.
- 실제 localhost PostgreSQL 7개: 최초 등록·기본 프로젝트 원자성, 동시 8회 가입 중복 방지, 기존 admin/UUID 보존, cookie CRUD·역할/소유권·inactive, 실제 Run 계획/승인/resume·GET/POST SSE·멱등 replay. 기업 SDK와 LLM은 double/mock이며 Executor 제출은 제외한다.
- 실제 localhost Redis 2개: 로그인 string과 Stream 공존·TTL·namespace 격리·정확한 key 폐기, BLOCK 중 별도 풀의 로그인 조회. 전용 난수 namespace만 생성·정리하며 기존 그룹·키는 변경하지 않는다.
- Node VM: 실제 Swagger inline JS의 cookie/CSRF 전달·외부 URL 비전송·401 처리·로그인 link, Demo JS syntax.
- 첫 전체 회귀: 853 passed / 27 failed. 26개는 업무 identity double의 추가 lookup이 SQL 계측에 포함됐고, 1개는 double이 인증용 DB 연결을 유지해 1-connection SSE fixture가 대기했다. double의 조회는 own short session으로 끝내고 fixture 표시가 있는 lookup만 계측에서 제외한다. 실제 production 활성/역할/접수 조회는 계속 계측한다. 기존 쿼리 수 기준을 완화하지 않았다.
- 최종 전체 API·Agent 회귀 **881 passed, 78 warnings, 2 subtests passed, 392.73초**. 위 SSO/Node/PG/Redis 시험은 이 전체 숫자에 포함되며 중복 합산하지 않는다. 경고는 기존 no-checkpointer 역할 graph의 durability 경고다.
- 배포 wheel 빌드와 `python -I scripts/diagnostics/validate_agent_package.py <wheel>` 통과. 소스 checkout 없이 import, service_auth 포함, test/auth_double 제외, 36개 OpenAPI 경로와 쿠키 보안/로그인·로그아웃, 12개 production Agent builder·prompt, mock graph 4단계를 확인했다.
- 변경 44개 파일의 Python syntax, 새 줄 whitespace, 변경 Markdown의 상대 링크, private endpoint 패턴 검사를 통과했다.

기존 업무 테스트의 header identity double은 테스트 패키지에만 존재한다. SSO 경계 테스트는 이 override를 제거하고 실제 쿠키 의존성을 검증한다. 기업 SDK 자체·사내 브라우저 왕복·실제 부하 성능 검증을 이 결과로 대신하지 않는다.

## 제한과 배포 전 절차

1. 사내 SDK 지원 request interface/검증 결과/복귀 URL/state 규약을 확인하고 위 두 함수를 연결한다. Flask 예제만으로 Starlette Request 호환을 가정하지 않는다. SDK 자체 network timeout도 설정한다.
2. 실제 API/프론트/SSO origin, HTTPS cookie, CORS credentials와 프록시 경로를 적용한다. 기본 Swagger CDN은 폐쇄망에서 로컬 자산으로 제공해야 할 수 있다. 실제 Gaia middleware 및 원본 router 인증은 별도 검증이 필요하다. Gaia body user_id는 로그인 근거가 아니다.
3. 기존 공개 ID와 사번의 대응을 명시적으로 이관하고 최초 관리자 ID를 실제 사번으로 bootstrap한다. 기존 일반 사용자에 관리자 자동 승격은 없다.
4. 로그인 TTL은 고정이며 요청마다 연장하지 않는다. 우리 세션 로그아웃은 사내 SSO 전체 로그아웃이 아니다. SDK 전역 폐기 신호가 없는 상태의 로컬 인증 유효기간은 TTL에 제한된다. 실행 중인 작업은 쿠키 만료로 취소하지 않는다.
5. 기존 Locust/진단 client의 X-User-Id는 호환되지 않는다. 쿠키·CSRF client로 이관해야 한다. 새 fake 인증 endpoint를 운영에 추가하지 않는다.
6. Redis 실제 설정은 maxmemory=0/noeviction이었다. 공존 기능 검증이며 메모리 수용량·로그인 부하 벤치마크는 아니다.

DB schema migration, 기존 실행/Agent 로직 변경, Executor 소스 변경, 컨테이너 재기동, 베이스 병합·원격 push·배포는 수행하지 않는다. 원본 checkout의 미커밋 변경은 보존한다.
