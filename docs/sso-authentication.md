# SSO 적용과 다른 서비스 재사용

106 · 2026-10-06. 사내 SDK 내부 요청 형태를 흉내 내던 코드를 제거했다.
원본 FastAPI 요청·서버 복귀 URL 전달, 쿠키 검증·직원 정보 조회를 구현했다. SDK 소스는 포함하지 않는다.
**폐쇄망에서는 생성 함수 한 곳의 실제 import/생성을 채우고 SDK를 설치해야 한다.**
실제 회사 로그인 왕복은 폐쇄망 검증 대상이다.

## API와 신원 경계

| API | 동작 |
|---|---|
| GET /api/v1/auth/login/sso | SDK 인증 확인 → 미인증 SSO 이동 / 인증된 직원 매핑·쿠키 발급·프론트 이동. 302 |
| GET /api/v1/auth/login/sso?target=docs | 같은 로그인 후 서버가 정한 Swagger 주소로 이동. 임의 URL 없음 |
| POST /api/v1/auth/logout | 쿠키·CSRF 검사 후 해당 서비스의 세션과 쿠키 폐기. 204 |
| GET /api/v1/users/me | 기존 사용자 정보 + csrf_token + login_expires_at. Cache-Control: no-store |

업무 API는 로그인 쿠키로 Redis에서 **내부 UUID**를 얻고 DB에서 활성 여부와 현재 role을
조회한다. `X-User-Id`, 요청 body의 user_id, Bearer 문자열은 쿠키 대신 인증하지 않는다.
사용자·관리자 권한과 프로젝트/세션/Run 소유권은 기존 서비스 계층에서 유지한다.
첫 로그인은 role=user 사용자·default 프로젝트·OWNER membership을 한 DB transaction에서
생성한다. 비활성 계정은 재활성화하지 않는다. 중복 최초 로그인은 기존 관리 advisory lock으로
직렬화하여 한 사용자로 수렴한다. 기존 계정은 이름·role·UUID·기본 프로젝트를 보존한다.

기존 공개 user_id와 검증된 사번을 정규화한 값이 일치하면 그 계정을 사용한다. 이름으로
다른 계정을 추측하지 않는다. 사번은 문자열이며 앞의 0을 제거하지 않는다.
**기존 공개 ID가 사번과 다른 계정은 전환 전에 명시적으로 연결 방안을 정해야 한다.**
이 구현은 임의의 과거 UUID/public ID에서 직원 사번을 알아내거나 기존 관리자 계정을
자동으로 새 직원에게 이전하지 않는다. 같은 사번의 기존 관리자는 기존 role을 유지한다.

## 폐쇄망 SDK 연결: 생성 함수 한 곳

파일: `src/dtest/infrastructure/sso/company.py`의 `_create_sdk`.
현재 이 함수는 명시적으로 NotImplementedError를 발생시킨다.
폐쇄망에서 **공식 SDK의 사용법**에 맞춰 실제 import·생성과 복귀 URL 설정을
채운다. SDK 소스나 회사 쿠키를 외부 저장소에 추가하지 않는다.

```python
def _create_sdk(request: Request, return_url: str | None = None) -> CompanySdk:
    # 공식 SDK 생성 방식으로 연결한다.
    # return_url이 주어지면 SDK가 지원하는 복귀 주소 설정으로 반영한다.
    # 사내 패키지 import 경로나 생성자 규격을 여기서 추측하지 않는다.
    ...
```

| 입력 | 의미 |
|---|---|
| request | 원본 FastAPI Request. Cookie 헤더·query 등은 변경하지 않음 |
| return_url=None | 직원 검증용 생성. 복귀 URL 생성 요청이 아님 |
| return_url=서버 URL | 로그인 URL 생성용. common runtime이 만든 신뢰할 복귀 주소 |

서비스는 SDK 내부의 args/to_dict/Flask 요청 형식을 재구현하지 않는다.
SsoArgs·SsoRequest는 삭제했다. SDK가 FastAPI 요청을 직접 받는지, 생성·복귀
주소 설정에 어떤 공식 API를 쓰는지는 폐쇄망의 실제 지원 방식으로 연결한다.
**원본 Request를 전달한다고 SDK가 바로 지원한다는 뜻은 아니다.**
SDK가 지원하지 않는다면 사내 공식 FastAPI 연동 방법이 필요하다.

직원 확인은 원본 Cookie 헤더 → `check_day_cookie(cookie)` → 검증 성공 시에만
`get_sso_info(cookie)` 순서다. 반환값은 Flask 예시의 대입 순서와 같은 다섯 값의
tuple 또는 list로 받는다. 사번·이름은 비어 있지 않은 문자열이어야 한다.
영문 이름·부서·메일은 문자열 또는 None이다. 다른 형식은 오류로 처리한다.
사번을 숫자로 바꾸지 않으며 모든 값을 SDK 검증 후에만 사용한다.

| SDK 결과 위치 / 이름 | VerifiedEmployee 필드 | 의미 |
|---|---|---|
| 0 / emp_no | employee_id | 직원 사번, 앞자리 0 보존 |
| 1 / emp_name | display_name | 직원 이름 |
| 2 / emp_name_en | english_name | 영문 이름 |
| 3 / dept | department | SDK가 제공하는 부서 값 |
| 4 / email | email | 회사 메일 주소 |
| 별도 만료 정보 없음 | valid_until_epoch | 현재 None, 서비스 로그인 TTL 사용 |

직원 정보는 UserDirectory.bind에 전부 전달한다. 현재 dtest의 DB 등록에는
사번과 이름을 사용한다. **추가 세 필드는 직원 계약에 포함되지만 User DB·users/me
응답에 저장/추가하지 않는다.** 기존 사용자 이름·role을 덮어쓰지 않고 최초 등록은
일반 사용자·기본 프로젝트 생성 정책을 따른다. Redis는 내부 UUID·CSRF·만료만 저장한다.

로그인 URL 생성 시 서버가 만든 복귀 주소를 return_url로 별도 전달한다.
원본 요청의 ORIGIN 등 query는 서비스가 가공하지 않는다. 폐쇄망 연결 함수는
**return_url을 복귀 주소로 사용해야 하며 사용자 query로 대체하면 안 된다.**
SDK의 redirect_url은 그대로 반환한다. common runtime이 이동 대상 origin을
검증하고 프론트 복귀 경로도 검사한다.

```text
GET /api/v1/auth/login/sso?return_to=/demo
  → 회사 Cookie 없거나 검증 false: SDK.redirect_url로 302
    서버 복귀 주소=https://api.example.internal/api/v1/auth/login/sso
           ?return_to=%2Fdemo&target=app
  → 회사 로그인 후 서버 복귀 주소로 이동: 회사 Cookie 검증, 직원 5개 값 조회
  → 사용자 연결/최초 등록, Redis 로그인 세션, HttpOnly 쿠키 발급
  → 설정된 프론트 /demo로 302
```

미인증만 None이고 SDK 장애·잘못된 성공 응답은 공통 runtime에서 503으로 처리한다.
예외 메시지·회사 쿠키·직원 값을 응답이나 로그에 기록하지 않는다.
동기 SDK는 SyncSsoAdapter가 thread pool에서 실행한다. SDK 자체의 연결/읽기
타임아웃도 공식 지원 방식으로 설정해야 한다. async deadline만으로 이미 실행 중인
thread를 종료할 수 없다. 별도 callback/state/nonce 규칙, 회사 쿠키 만료·전역
로그아웃 규칙은 사내 가이드대로 폐쇄망에서 확인한다.

## 중앙 설정

로컬은 config.yml, 배포는 config.dev.yml/config.stg.yml/config.prd.yml에
최상위 키로 설정한다. APP_ENV로 배포 환경을 선택하며 YAML > env > 기본값 순서다.
아래 도메인은 예시이며 실제 등록된 주소로 바꾼다. Redis는 기존 REDIS_URL을 사용한다.

```yaml
SSO_ADAPTER_FACTORY: dtest.infrastructure.sso.company:create_adapter
SSO_PUBLIC_API_ORIGIN: https://api.example.internal
SSO_FRONTEND_ORIGIN: https://ui.example.internal
SSO_ALLOWED_ORIGINS: [https://sso.example.internal]
SSO_NAMESPACE: dtest-agent:prd:sso
SSO_COOKIE_NAME: __Host-dtest_session
SSO_COOKIE_SECURE: true
SSO_COOKIE_SAMESITE: lax
SSO_SESSION_TTL_SECONDS: 1800
SSO_AUTO_REGISTER: true
SSO_ALLOWED_RETURN_ROOTS: ["/", "/demo", "/projects"]
SSO_REDIS_MAX_CONNECTIONS: 8
SSO_REDIS_TIMEOUT_SECONDS: 3
SSO_CALL_TIMEOUT_SECONDS: 10
```

SDK 생성 코드가 미구현이면 위 factory를 선택해도 실제 로그인은 503이다.
환경변수는 새로 추가하지 않았다. 서비스 세션을 위해 Flask SECRET_KEY나
Flask SessionMiddleware를 추가할 필요는 없다.

환경변수는 같은 이름의 대문자다. 목록은 env에서 JSON 배열을 사용한다.
namespace 미지정 시 `dtest-agent:{APP_ENV}:sso`로 정한다. 리다이렉트는 설정된 origin으로
만들며 요청의 Host/X-Forwarded-Host를 신뢰해 로그인 복귀 주소를 생성하지 않는다.
origin은 scheme://host[:port]이고 경로를 넣지 않는다. 공개 API prefix는 API_V1_PREFIX다.

| 설정 | 기본값 / 의미 |
|---|---|
| SSO_ADAPTER_FACTORY | 빈 값. 미설정 로그인 503. Python module:factory(settings) 규격 |
| SSO_PUBLIC_API_ORIGIN / SSO_FRONTEND_ORIGIN | 없음. 실제 외부 API·프론트 origin 필요 |
| SSO_ALLOWED_ORIGINS | 빈 목록. SDK 로그인 이동 대상 allowlist |
| SSO_COOKIE_NAME / SSO_COOKIE_SECURE / SSO_COOKIE_SAMESITE | dtest_session / true / lax. 서비스마다 쿠키 이름 분리 |
| SSO_SESSION_TTL_SECONDS | 고정 1800초, 60~86400. 요청할 때 연장하지 않음 |
| SSO_AUTO_REGISTER | true. 일반 사용자+기본 프로젝트 생성, 관리자 자동 부여 없음 |
| SSO_ALLOWED_RETURN_ROOTS | 코드 기본 ["/"], 공통 YAML ["/", "/projects"]. 프론트 복귀 경로 제한 |
| SSO_REDIS_MAX_CONNECTIONS | 8, 1~128. Streams와 별도 로그인 연결풀 |
| SSO_REDIS_TIMEOUT_SECONDS | 3초. Redis 연결·명령 기한 |
| SSO_CALL_TIMEOUT_SECONDS | 10초. SDK coroutine 기한; SDK 자체 네트워크 기한도 설정 |

HTTP 로컬은 명시적으로 SSO_COOKIE_SECURE=false를 쓰고 __Host- 이름을 사용하지 않는다.
SameSite=None은 Secure가 필수다. 다른 사이트의 프론트 배포는 쿠키·CORS 정책 확인이 필요하다.
Swagger는 API와 같은 origin의 페이지를 사용한다. 앱은 여기에 CORS origin을 임의 추가하지 않는다.

## Redis 사용

기존 REDIS_URL의 서버와 DB를 함께 사용한다. 로그인 키는
`{namespace}:login:{SID의 SHA-256}`이고 string 값에는 내부 User ID·CSRF 값·만료 시각만
저장한다. 회사 쿠키, 직원 이름/부서/이메일, 역할, 분석 데이터는 저장하지 않는다.
브라우저 쿠키는 암호학적 난수 SID이며 HttpOnly, Path=/, 설정된 Secure/SameSite를 사용한다.
로그인 때 SID를 새로 만들고 이전 SID를 폐기한다. 로그인 키에만 TTL을 부여하고 로그아웃은
해당 key만 지운다. FLUSHDB, Stream 그룹/ACK 수정, 공유 Stream TTL 설정은 하지 않는다.

각 API process당 로그인 Redis client/pool을 한 번 구성하고 lifespan에서 종료한다.
Streams의 BLOCK 연결이 로그인 연결풀을 점유하지 않으며, 같은 서비스의 모든 Pod는 같은
namespace를 사용한다. 서버의 CPU/메모리/장애는 공유되므로 namespace가 자원 격리를
제공하는 것은 아니다. 로컬 확인 결과 maxmemory=0, maxmemory-policy=noeviction이었다.
실제 운영 메모리/보존 정책까지 검증했다는 의미는 아니다.

회사 SDK가 인증 만료를 제공하면 그 시각보다 로컬 세션이 오래 가지 않는다. 만료 정보를
제공하지 않으면 설정된 로컬 TTL을 사용한다. 회사 전역 로그아웃을 즉시 감지하는 기능은
알 수 없어 구현하지 않았다. 로컬 로그아웃은 회사 SSO 전체 로그아웃이 아니며, 회사 SSO가
유효하면 로그인 URL을 다시 열 때 재로그인될 수 있다. 이미 접수된 Agent/Executor 작업을
로그인 만료/로그아웃으로 취소하지 않는다. 기존 장기 실행·세션 잠금 정책을 유지한다.

## Swagger와 프론트 테스트 순서

1. `_create_sdk`의 import/생성을 채우고 같은 환경에 사내 라이브러리를 설치한다. SDK 소스/비밀값을 외부 Git에 올리지 않는다.
2. 설정의 origin/SSO 허용 주소/SDK factory를 주입하고 기존 root app.py로 기동한다.
3. 로컬 공통 app은 `/docs`, 플랫폼 생성 app에 attach할 때는 제공된 `/docs`를 건드리지 않고 `/service/docs`를 연다.
4. **SSO 로그인** 링크로 브라우저 이동한다. 로그인 API의 Try it out으로 SSO 화면을 열지 않는다.
5. SDK에 등록된 복귀 주소를 거쳐 쿠키를 발급받으면 해당 Swagger로 돌아온다.
6. `/users/me`로 이름·역할·기본 프로젝트를 확인한다. Swagger가 CSRF를 메모리에 보관한다.
7. POST/PATCH/DELETE/PUT에는 requestInterceptor가 X-CSRF-Token을 자동 추가한다. 쿠키 SID를 Authorize에 복사하지 않는다.
8. 로그인 갱신 후에는 로그인 상태 확인 버튼을 눌러 CSRF를 갱신한다. 로그아웃/만료 뒤 업무 API는 401이다.

Swagger interceptor는 같은 origin의 API prefix에만 CSRF를 보낸다. 외부 OpenAPI나 다른
서비스 URL에는 보내지 않는다. 사용자 정보는 textContent로 표시한다. 기존 FastAPI Swagger
CDN 자산을 사용하므로 폐쇄망에서는 플랫폼이 제공하는 로컬 자산 경로와 연결해야 할 수 있다.
reverse-proxy 하위 경로(root_path)나 플랫폼 인증 middleware와의 실제 조립은 해당 환경에서 확인해야 한다.

프론트 요청은 credentials를 포함한다. GET /users/me에서 받은 csrf_token을 변경 요청의
X-CSRF-Token에 넣는다. GET SSE는 쿠키, POST SSE는 쿠키+CSRF를 사용한다.
401은 로그인 화면 안내, 403은 권한/CSRF 오류다. 일반 API를 SSO HTML로 리다이렉트하지 않는다.

```javascript
const response = await fetch('/api/v1/users/me', {credentials:'include'});
if (response.status === 401) {
  window.location.assign('/api/v1/auth/login/sso');
} else if (response.ok) {
  const me = await response.json();
  await fetch('/api/v1/projects', {
    method:'POST', credentials:'include',
    headers:{'Content-Type':'application/json','X-CSRF-Token':me.csrf_token},
    body:JSON.stringify({project_name:'새 프로젝트'})
  });
}
```

## 다른 서비스 재사용

`dtest.contracts.auth`는 직원·어댑터·UserDirectory 계약이고,
`dtest.infrastructure.sso`는 SDK 연결, `dtest.api_service.auth`는 로그인 HTTP 경계다.
직원 매핑 정책은 `dtest.application.resources.sso_users`에 둔다.
공통 attach_sso는 기존 FastAPI app에 auth 라우터와 runtime을 연결하는 조립 함수다.
새 서버나 lifespan을 만들지 않는다. 각 서비스는 UserDirectory.bind에서 직원↔자체 User ID
매핑/등록 정책을 구현하고 자신의 Actor Dependency에서 get_login_session 결과의 user_id로
활성 여부·권한·소유권을 검사한다. 공통 쿠키 세션만으로 DB 권한 검사가 대체되지는 않는다.

```python
auth = attach_sso(
    app,
    settings=sso_settings,
    users=service_user_directory,
    redis_url=service_redis_url,
    api_prefix="/api/v1",
    docs_path="/docs",
)
# 기존 lifespan 종료 시 await auth.close()
# 업무 라우트는 Depends(get_login_session) 또는 이를 사용하는 자체 Actor Dependency로 보호.
```

API·Agent는 같은 Pod에 남고 DB UUID 전달과 Worker 동작은 바뀌지 않는다. Gaia의 플랫폼
별도 호출 경로는 사용자용 쿠키 API와 신뢰 경계가 다르므로 body user_id를 그대로 SSO 신원으로
인정하지 않는다. 이번 작업으로 아직 미구현인 Gaia adapter 인증이 완성됐다고 보지 않는다.

## 전환과 검증

추가 DB schema migration은 없다. 기존 public ID·role revision을 유지한다.
첫 관리자는 그 직원이 자동 일반 사용자로 등록되기 **전에** 현재 관리자 초기화
명령으로 검증될 사번과 같은 공개 ID를 준비한다. 일반 사용자 등록 후에는 기존
관리자 API로 권한을 변경한다. bootstrap은 기존 일반 계정을 자동 승격하지 않는다.

```sh
PYTHONPATH=src python -m dtest.application.admin \
  --user-id 실제사번 --user-name 관리자
```

기존 X-User-Id/Bearer 부하테스트·HTTP 진단 scripts는 현재 API에 그대로 호환되지 않는다.
실제 부하테스트에는 명시적으로 발급한 테스트 사용자별 로그인 쿠키·CSRF를 입력하도록
클라이언트를 이관해야 한다. 운영 서버에 헤더 우회나 mock 로그인 endpoint를 추가하지 않았다.
과거 업무 회귀는 test-only dependency override로 기존 테스트 신원을 재현하며 wheel에 포함되지 않는다.
새 SSO 검증은 그 override를 제거하고 실제 쿠키 경계를 사용한다. 회사 SDK만 test double이다.

최신 변경·검증은 [106 작업 기록](improvements/106-sso-sdk-boundary.md),
직원 필드 추가 이력은 [105](improvements/105-company-sso-adapter.md),
기존 세션 정책은 [045 작업 기록](improvements/045-sso-authentication.md)을 참고한다.
브라우저 회사 SSO 왕복·회사 쿠키 정책·실제 SDK의 직원 정보 검증은 폐쇄망에서 남아 있다.


## 외부 대기를 포함하는 조회의 인증 DB 수명

Workflow 검색은 ReadUserId를 사용한다. 로그인 쿠키·CSRF·활성 사용자 검사를
통과한 뒤 인증용 짧은 DB 세션을 닫고 임베딩 요청을 시작한다. 검색 자체가
데이터를 바꾸지 않으므로 사용자 공유 잠금을 유지하지 않는다.

이 권한은 접수 시점의 snapshot이며 검색 중 비활성화된 계정의 이미 시작된
읽기는 끝날 수 있다. 변경 API에는 이 의존성을 사용하지 않는다. CurrentUserId는
사용자 삭제와 변경 접수의 경합을 막는 기존 공유 잠금을 유지한다. 자세한 변경과
실제 단일 연결 pool 검증은 [113 작업 기록](improvements/113-workflow-search-db-scope.md)을 따른다.
