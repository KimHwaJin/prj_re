# SSO 적용과 다른 서비스 재사용

045 · 2026-10-01. 회사 라이브러리는 외부 제공이 불가능하다는 사용자 지시에 따라 소스와
추측한 SDK 호출을 포함하지 않았다. **공통 로그인·Redis 세션·기존 API 인증·Swagger는
구현했고, 실제 회사 SSO는 폐쇄망의 SDK 연결 함수 두 곳을 완성해야 사용할 수 있다.**

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

## 폐쇄망 SDK 연결: 여기 두 함수만 작성

`src/integrations/company_sso.py`:

```python
def verify_employee(request) -> VerifiedEmployee | None:
    # 공식 사내 SDK로 요청의 원본 쿠키를 검증한다.
    # SDK 검증 결과의 공식 getter에서 사번·이름을 추출한다.
    # return VerifiedEmployee(employee_id=사번, display_name=이름,
    #                         valid_until_epoch=만료시각_초단위_있을때)
    # 미인증만 None. 통신/SDK 장애는 예외.
    ...

def build_login_url(request, return_url: str) -> str:
    # 공식 SDK의 로그인 URL/ORIGIN/복귀 주소 규격을 따른다.
    ...
```

실제 배포 파일에는 `...`가 아니라 구현이 필요하다. 현재 연결 함수는 명시적으로
NotImplementedError를 발생시킨다. 미설정 또는 미구현 SDK를 정상 로그인으로 취급하지 않는다.
제공받은 Flask 예시의 SSO(request), check_day_cookie, redirect_url 외에는 SDK 규격을
알 수 없다. FastAPI Request 호환성, 직원 getter, 별도 callback의 GET/POST, state/nonce,
쿠키 만료·전역 로그아웃 규칙은 사내 가이드를 보고 **폐쇄망에서** 확인한다.
callback은 필요 여부·규격을 알 수 없어 임의로 만들지 않았다.

동기 SDK는 SyncSsoAdapter가 thread pool에서 실행한다. 이는 Flask의 request/session 전역을
자동 흉내 내는 기능이 아니다. SDK 생성·요청 변환은 공식 호환 방식으로 구현하고 SDK의
자체 연결/읽기 타임아웃도 설정한다. 공통 async deadline만으로 실행 중인 thread를 종료할 수 없다.

## 중앙 설정

`service.auth`를 config.dev.yml/config.stg.yml/config.prd.yml 또는 별도 선택 YAML에 넣는다.
YAML > env > 기본값 순서를 유지한다. 아래 도메인은 예시이며 실제 등록된 주소로 바꾼다.

```yaml
service:
  auth:
    sso_adapter_factory: integrations.company_sso:create_adapter
    sso_public_api_origin: https://api.example.internal
    sso_frontend_origin: https://ui.example.internal
    sso_allowed_origins: [https://sso.example.internal]
    sso_namespace: dtest-agent:prd:sso
    sso_cookie_name: __Host-dtest_session
    sso_cookie_secure: true
    sso_cookie_samesite: lax
    sso_session_ttl_seconds: 1800
    sso_auto_register: true
    sso_allowed_return_roots: ["/", "/projects"]
    sso_redis_max_connections: 8
    sso_redis_timeout_seconds: 3
    sso_call_timeout_seconds: 10
```

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

HTTP 로컬은 명시적으로 sso_cookie_secure=false를 쓰고 __Host- 이름을 사용하지 않는다.
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

1. SDK 두 함수를 구현하고 같은 환경에 사내 라이브러리를 설치한다. SDK 소스/비밀값을 외부 Git에 올리지 않는다.
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

service_auth 패키지에는 api_service/agent_service/애플리케이션 DB import가 없다.
공통 attach_sso는 기존 FastAPI app에 auth 라우터와 runtime을 연결하는 조립 함수다.
새 서버나 lifespan을 만들지 않는다. 각 서비스는 UserDirectory.bind에서 직원↔자체 User ID
매핑/등록 정책을 구현하고 자신의 Actor Dependency에서 get_login_session 결과의 user_id로
활성 여부·권한·소유권을 검사한다. 공통 쿠키 세션만으로 DB 권한 검사가 대체되지는 않는다.

```python
auth = attach_sso(app, settings=sso_settings, users=service_user_directory,
                  redis_url=service_redis_url, api_prefix='/api/v1', docs_path='/docs')
# 기존 lifespan 종료 시 await auth.close()
# 업무 라우트는 Depends(get_login_session) 또는 이를 사용하는 자체 Actor Dependency로 보호.
```

API·Agent는 같은 Pod에 남고 DB UUID 전달과 Worker 동작은 바뀌지 않는다. Gaia의 플랫폼
별도 호출 경로는 사용자용 쿠키 API와 신뢰 경계가 다르므로 body user_id를 그대로 SSO 신원으로
인정하지 않는다. 이번 작업으로 아직 미구현인 Gaia adapter 인증이 완성됐다고 보지 않는다.

## 전환과 검증

추가 DB schema migration은 없다. 기존 public ID·role revision을 유지한다. 첫 관리자는
그 직원이 자동 일반 사용자로 등록되기 **전에** 기존 bootstrap_admin 명령으로 검증될 사번과
같은 공개 ID를 사용해 준비한다. 이미 일반 사용자/다른 관리자가 존재하면 해당 명령이
자동 승격하지 않으므로 기존 관리자 계정 연결·권한 조정을 먼저 정한다.

```sh
PYTHONPATH=src python -m bootstrap_admin --user-id 실제사번 --user-name 관리자
```

기존 X-User-Id/Bearer 부하테스트·HTTP 진단 scripts는 현재 API에 그대로 호환되지 않는다.
실제 부하테스트에는 명시적으로 발급한 테스트 사용자별 로그인 쿠키·CSRF를 입력하도록
클라이언트를 이관해야 한다. 운영 서버에 헤더 우회나 mock 로그인 endpoint를 추가하지 않았다.
과거 업무 회귀는 test-only dependency override로 기존 테스트 신원을 재현하며 wheel에 포함되지 않는다.
새 SSO 검증은 그 override를 제거하고 실제 쿠키 경계를 사용한다. 회사 SDK만 test double이다.

테스트·결과·제한은 [045 작업 기록](improvements/045-sso-authentication.md)을 참고한다.
브라우저 회사 SSO 왕복·회사 쿠키 정책·실제 SDK의 직원 정보 검증은 폐쇄망에서 남아 있다.
