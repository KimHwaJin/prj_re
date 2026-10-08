# SSO 적용과 다른 서비스 재사용

124 · 2026-10-08. 각 로그인302의 목적지 분기·callback 표시·Cookie 헤더 유무를 진단한다.
미인증 callback의 반복302는401로 멈춘다.
SDK용 ORIGIN에 서버가 만든 로그인 복귀 URL을 연결한다.
SDK 요청 호환과 로그인 URL 검증 거절 사유 진단을 유지한다.
확인된 사내 SDK 요청 접근은 url·args·cookies·environ이다.
동기 SDK용 요청 view가 URL 문자열, args/cookies의 to_dict, WSGI식 environ을
제공한다. 나머지는 원본 FastAPI 요청으로 위임한다. SDK 소스는 포함하지 않는다.
**폐쇄망에서는 생성 함수 한 곳의 실제 import/생성을 채우고 SDK를 설치해야 한다.**
실제 회사 로그인 왕복은 폐쇄망 검증 대상이다.

## API와 신원 경계

| API | 동작 |
|---|---|
| GET /api/v1/auth/login/sso | SDK 인증 확인 → 최초 미인증은302 SSO 이동 / 복귀 미인증은401 / 인증된 직원은 쿠키 발급·302 프론트 이동 |
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
| request | SyncSsoAdapter 요청 view. url·args·cookies·environ을 호환하고 나머지는 원본 Request에 위임 |
| return_url=None | 직원 검증용 생성. 복귀 URL 생성 요청이 아님 |
| return_url=서버 URL | 로그인 URL 생성용. common runtime이 만든 신뢰할 복귀 주소 |

사용자는 `SSO(request)`에서 `attribute=startswith object_type=URL`을 보고했다.
FastAPI의 request.url은 Starlette URL 객체이므로 문자열 전용 startswith가 없다.
`src/dtest/infrastructure/sso/request.py`의 SdkRequestView가 url을 str로 제공한다.
이후 `attribute=args object_type=Request`와 `attribute=environ` 로그에 따라
args와 environ도 제공한다.
SyncSsoAdapter가 직원 검증·로그인 URL 생성 모두에 이 view를 전달하므로
폐쇄망 `_create_sdk`의 `sso = SSO(request)` 코드는 그대로 사용한다.
FastAPI 라우터·다른 async 어댑터의 요청 객체는 바꾸지 않는다.

기존 private factory의 Request 타입 주석은 호환을 위해 유지하며, 런타임에
받는 값은 Request 인스턴스가 아니라 속성 조회를 위임하는 view다. 외부 SDK의
속성 접근 계약을 연결하는 경계에서만 명시적으로 cast한다. SDK가 Request의
실제 클래스 정체성을 요구하거나 다른 Flask 전용 속성을 요구하는지는 별도
확인이 필요하다. Flask session은 구현하지 않는다. 로그인 URL 생성 때만
SDK용 args의 ORIGIN을 서버 return_url로 치환한다. 원본 ASGI query_params는
바꾸지 않으며 직원 검증에서는 기존 args를 유지한다. 서버 return_url은
private factory의 별도 인자로도 계속 전달한다.

### SDK의 request.args 호환

SDK의 `request.args.to_dict()`는 FastAPI `request.query_params`를 읽는
SdkQueryArgs로 연결한다. get, 키 조회, in, 반복과 to_dict(flat=True/False)를
제공한다. 빈값·URL decoding·중복 키를 보존하며, Flask MultiDict 규칙대로
단일 조회와 flat=True는 첫 값을, flat=False는 모든 값의 새 list를 반환한다.
원본 QueryParams의 단일 조회는 마지막 값이지만 이를 변경하지 않는다.
반환 dict/list를 수정해도 원본 요청과 다른 조회 결과는 바뀌지 않는다.
args는 읽기 전용이며 Flask 전체 Request/MultiDict 구현은 아니다.
SDK가 요구하는 다른 속성이 확인되면 추가 경계를 별도로 검토한다.

### SDK의 request.cookies.to_dict 호환

사용자 확인에 따라 SDK의 to_dict 호출 대상은 args와 cookies다. FastAPI의
request.cookies는 일반 dict이므로 직접 to_dict를 호출하면 AttributeError가
발생한다. SdkRequestView.cookies는 SdkCookies라는 읽기 전용 매핑을 제공한다.
SdkQueryArgs와 SdkCookies는 공통 SdkRequestValues의 get/키 조회/in/반복 및
복사본 to_dict(flat=True/False)를 공유한다.

쿠키는 원본 Request.cookies의 파싱된 값을 복사하며 재파싱·URL decoding을 하지
않는다. 빈 쿠키, 따옴표, %·+를 포함한 값, 중복 쿠키의 Starlette 파싱 결과를
그대로 보존한다. flat=True는 dict[str, str], flat=False는 값별 새 list를 가진
dict를 제공한다. 반환 dict/list를 수정해도 원래 쿠키/헤더는 바뀌지 않는다.

확인된 SDK 접근 계약:

| 접근 | SDK용 제공 값 |
|---|---|
| request.url.startswith(...) | 문자열 URL |
| request.args.to_dict() / get(...) | 쿼리 매핑·복사본 dict. 로그인 URL 생성의 ORIGIN은 서버 callback |
| request.cookies.to_dict() / get(...) | 기존 파싱 결과의 쿠키 매핑·복사본 dict |
| request.environ["HTTP_HOST"] | 실제 Host 헤더 기반 메타데이터 사본 |
| request.headers.get("cookie") | 원본 Starlette 헤더 조회, 전체 Cookie 문자열 유지 |

이 표는 확인된 SDK 입력 범위다. 전체 Flask Request/MultiDict·세션·WSGI 서버를
구현했다는 뜻은 아니다. SDK import/생성 및 공식 복귀 주소 설정은 company.py의
기존 폐쇄망 연결을 유지한다. 실제 회사 인증 왕복은 내부망에서 검증한다.

### SDK의 request.environ 호환

`src/dtest/infrastructure/sso/environ.py`가 ASGI scope/headers를 WSGI식 요청
메타데이터로 변환한다. SdkRequestView.environ은 SDK에 전달할 요청별 dict 사본이다.
SDK가 사본을 바꿔도 원본 ASGI scope/headers나 다른 요청에 반영되지 않는다.

| environ 키 | 원천 / 의미 |
|---|---|
| REQUEST_METHOD | scope.method |
| SCRIPT_NAME / PATH_INFO | root_path / mount prefix를 제외한 path. WSGI UTF-8→Latin-1 표현 |
| QUERY_STRING | 원본 query_string 바이트의 Latin-1 표현; 재인코딩·순서 변경 없음 |
| SERVER_PROTOCOL | scope.http_version 기반 HTTP 버전 |
| SERVER_NAME / SERVER_PORT | scope.server. 정보가 없으면 빈 문자열, Unix socket의 None port도 빈 문자열 |
| REMOTE_ADDR / REMOTE_PORT | scope.client. 정보가 없으면 해당 키 생략 |
| wsgi.url_scheme | scope.scheme |
| HTTP_HOST / HTTP_USER_AGENT / HTTP_COOKIE 등 | 헤더명을 대문자·밑줄로 변환한 HTTP_* 키 |
| CONTENT_TYPE / CONTENT_LENGTH | HTTP_ 접두사 없이 제공하는 예외 헤더 |

중복 Cookie는 `; `로, 다른 중복 헤더는 `,`로 연결한다. header bytes는 Latin-1로
보존한다. forwarded 헤더는 HTTP_* 메타데이터로 전달하되, 이 어댑터에서
REMOTE_ADDR나 scheme을 덮어쓰지 않는다. 프록시 신뢰 처리는 ASGI 서버가 맡는다.

이는 전체 WSGI 서버가 아니다. wsgi.input/errors·Flask session·OS 환경변수·
ASGI scope 내부 상태는 복제하지 않으며 요청 본문을 소비하지 않는다.
SDK가 추가 environ 키를 요구하면 그 키와 공식 계약을 확인해 별도로 반영한다.
실제 SDK가 읽는 키는 폐쇄망에서 확인해야 하며 전체 SDK 호환이 검증된 것은 아니다.

매핑 기준: [ASGI HTTP 명세](https://asgi.readthedocs.io/en/stable/specs/www.html),
[WSGI PEP3333](https://peps.python.org/pep-3333/).

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
사용자가 설명한 SDK는 request.args.to_dict()의 ORIGIN을 읽는다. 공용
SyncSsoAdapter.login_url이 sdk_request(request, return_url)을 만들며 SDK용
args의 모든 기존 ORIGIN을 제거하고 서버 callback 하나만 넣는다. 원본 ASGI
query_params와 다른 query의 값/중복은 보존하고, verify에서는 ORIGIN을 바꾸지 않는다.
**SDK 복귀 주소는 서버가 만든 값이며 사용자 query로 대체하지 않는다.**
SDK의 공식 redirect_url 생성을 그대로 사용한다. common runtime이 로그인
이동 origin과 최종 프론트 복귀 경로를 검사한다.

```text
GET /api/v1/auth/login/sso?return_to=/demo
  → 회사 Cookie 없거나 검증 false: SDK.redirect_url로 302
    서버 복귀 주소=https://api.example.internal/api/v1/auth/login/sso
           ?return_to=%2Fdemo&target=app&sso_callback=true
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

### return_url을 어디에 넣는가

1. 로컬 config.yml의 SSO_PUBLIC_API_ORIGIN에 API origin만 설정한다.
   예: http://localhost:5000. /api/v1/auth/login/sso 경로는 넣지 않는다.
2. 로그인 API가 API_V1_PREFIX와 /auth/login/sso, return_to/target query
   및 sso_callback=true를 결합해 callback 전체를 생성한다. 별도 복귀 URL을
   config에 직접 입력하지 않는다.
3. SyncSsoAdapter.login_url이 SDK 요청 view의 args["ORIGIN"]에 callback을 넣는다.
4. 폐쇄망 company.py의 _create_sdk는 기존 SSO(request)를 그대로 호출한다.
   SDK 내부의 ORIGIN 처리·redirect_url 생성 기능이 이 값을 사용한다.

예시 설정:

```yaml
SSO_PUBLIC_API_ORIGIN: "http://localhost:5000"
SSO_FRONTEND_ORIGIN: "http://localhost:5000"
SSO_COOKIE_SECURE: false
SSO_ALLOWED_ORIGINS: ["http://sso.example.internal"]
SSO_ALLOWED_RETURN_ROOTS: ["/", "/demo", "/projects"]
```

정상 API URL: /api/v1/auth/login/sso?return_to=%2Fdemo (등호가 있어야 한다).
API 기본 prefix일 때 SDK용 ORIGIN은 다음 전체 주소다.

    http://localhost:5000/api/v1/auth/login/sso?return_to=%2Fdemo&target=app&sso_callback=true

SDK가 redirect_uri에 넣을 때 공식 방식으로 인코딩한다. SDK URL에 callback을
문자열로 이어 붙이거나 먼저 임의로 quote하지 않는다. args.to_dict()는 사본이므로
private factory에서 반환 dict 하나를 바꾸는 것만으로 Request가 바뀌지는 않는다.
공용 request view의 ORIGIN 연결을 사용하고 내부 factory의 별도 URL 재작성은
중복되지 않게 실제 코드/SDK 공식 계약을 확인한다.

302 자체는 실패가 아니다. Location을 순서대로 확인한다.

    로그인 API → 302 회사 SSO
    회사 로그인 → API callback
    API callback → 302 /demo + 서비스 로그인 Cookie
    GET /api/v1/users/me → 200

123부터 callback에서 SDK가 직원을 검증하지 못하면 다시 SSO로 이동하지 않고
HTTP401로 끝낸다. 생성된 복귀 URL의 sso_callback=true가 최초 요청과 복귀 요청을
구분한다. 이 표시는 인증 증명·OAuth state가 아니며, 사용자가 직접 붙여도
SDK 인증을 우회하거나 세션을 발급받지 못한다. 정상 SDK 검증 성공은 계속302다.

로그는 uv run app.py --env local을 실행한 **앱 터미널**에서 확인한다.
Uvicorn의 GET ...302 줄은 접속 로그이며 아래 항목은 auth runtime의 분기 로그다.
기본 앱 진입점은 INFO 이상을 출력한다. 플랫폼 별도 로거를 쓰면
`dtest.api_service.auth.runtime`의 INFO가 필터링되지 않는지 확인한다.

```text
sso_login_redirect destination=corporate_sso callback=False cookie_header_present=False
sso_login_redirect destination=application callback=True cookie_header_present=True
sso_callback_unverified cookie_header_present=False
```

- corporate_sso: SDK 직원 검증이 None이며 회사 SSO로302를 보냄. 최초 로그인에는
  정상이다. 실제 회사 왕복 후에도 이 줄만 반복되면 callback 표시가 보존되지 않는
  경로인지 Network에서 확인한다. 이 로그 하나로 SDK의 문제를 확정하지 않는다.
- application: SDK 직원 검증과 서비스 세션 발급이 끝났고 프론트 또는 docs로302를
  보냄. 이후 반복은 실제 목적지·쿠키 저장 및 인증 조회를 확인한다.
- callback=True/False: 서비스가 해석한 sso_callback boolean이다. 인증 증명이 아니다.
- callback_unverified: 표시가 유지된 복귀에서 미인증이라401로 멈춤.

아래 Cookie 헤더 유무 설명은 미인증 callback에 적용한다.

- False: 복귀 요청의 Cookie 헤더가 없거나 비었다. 현재 company adapter는 이때
  SDK 검증 없이 None을 반환한다. 회사 쿠키 전달 또는 공식 callback 처리 규칙을 확인한다.
- True: 어떤 Cookie 헤더는 있다. 회사 쿠키가 있다는 뜻이나 유효하다는 뜻은 아니다.
  현재 company adapter의 check_day_cookie 결과와 SDK의 공식 복귀 처리를 확인한다.
- sso_sdk_failed: SDK 호출 예외다. 미인증 None과 다르며 HTTP503으로 끝난다.

브라우저 Network의 Preserve log로 호스트/경로·상태코드와 callback의 Cookie
헤더 유무만 확인한다. 회사 쿠키 값·ticket·전체 redirect_url을 공유하지 않는다.
회사 도메인의 쿠키가 localhost에 그대로 전달되는 것으로 가정하면 안 된다.
Cookie Domain은 전송 대상 호스트를 제한하며, 회사 서버가 무관한 localhost
도메인의 쿠키를 직접 설정할 수는 없다. SDK가 공식적으로 ticket을 처리하거나
로컬용 쿠키를 만드는 절차가 있다면 그 계약에 맞춰 연결해야 한다.
참고: [MDN Cookie Domain](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Set-Cookie#domain).

확인용 URL에 sso_callback=true를 직접 넣어401을 확인한 것만으로는 실제 회사
왕복을 검증한 것이 아니다. /demo 로그인 버튼으로 실제 왕복을 확인해야 한다.

이 변경은 미인증 callback의 재시도를 멈추는 것이며 실제 인증 실패 원인 자체를
해결했다는 뜻은 아니다. 회사 SSO가 callback query를 제거하거나 다른 주소로
보내면 이 표시도 사라진다. 그 경우 실제 Location과 공식 허용 복귀 주소를
확인해야 한다. 이 표시를 회사 인증 프로토콜의 nonce/state 대신 사용하지 않는다.
공통 API를 fetch/Try it out으로 호출해 SSO 화면을 가져오기보다는 브라우저
로그인 링크로 이동한다. client_id 및 허용 redirect_uri 등록 규칙은 사내 명세로
확인하고, 실제 SDK가 완성한 redirect_uri가 위 callback인지 내부망에서 확인한다.

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

## SDK 속성 오류 진단과 내부망 환경 보존

116 · 2026-10-08. `sso_sdk_failed`는 예외 타입 외에 누락된 속성명과
실패한 객체의 타입명을 기록한다.

```text
sso_sdk_failed error_type=AttributeError attribute=args object_type=Request
```

이 예시는 SDK가 Request.args에 접근한 경우의 진단 형식이다. 실제 SDK가
Flask 요청을 요구한다고 확정한 결과는 아니다. `redirect_url`/SDK 타입이면
생성 결과·속성 계약을, `NoneType`이면 앞 단계의 반환값을 확인한다.
이 로그는 인증 실패의 근본 원인을 자동으로 수정하지 않는다.

Python AttributeError의 name/obj 메타데이터를 사용한다. 속성명·타입명은
최대128자 identifier만 허용하며 누락·부적합 값은 unknown이다. 예외 원문,
객체 repr, Cookie 헤더, 직원 정보, redirect URL은 기록하지 않는다.
직접 raise한 AttributeError는 속성 정보가 없어서 unknown일 수 있다.
클라이언트 응답은 기존503 Corporate SSO is unavailable을 유지한다.

116~122 진단·요청 호환 변경은 pyproject.toml·uv.lock·SDK 연결
company.py를 수정하지 않는다.
0a522a2 이후 이번 업데이트만 받는 경우 두 의존성 파일의 원격 변경은0이다.
로컬 내부망 의존성을 되돌리거나 skip-worktree/강제 checkout을 설정하지 않는다.
더 오래된 기준에서 업데이트할 때 과거 dependency 변경이나 다른 개발자의
변경과 충돌하지 않는다고 보장하지 않는다.

기존 내부망 환경이 준비돼 있다면 앱 종료 후 코드를 받고 자동 sync 없이
다시 실행할 수 있다.

```powershell
git pull --ff-only
uv run --no-sync app.py --env local
```

--no-sync는 현재 가상환경의 패키지를 유지하는 실행 옵션이다. 누락 의존성을
설치하거나 설정 오류를 해결하는 옵션은 아니다. 이번 변경에는 새 의존성이 없다.
SDK 소스와 회사 인증 정보는 외부 저장소에 공유할 필요 없다.

## SDK 로그인 URL의 502 진단

`Corporate SSO returned an invalid login URL.`은 SDK 함수가 값을 반환한 뒤
공통 runtime의 로그인 이동 주소 검증에서 거절됐다는 뜻이다. 직원 인증 성공이나
회사 로그인 왕복 완료를 의미하지 않는다. 일반 COMMON-INTERNAL_ERROR 식별자는
공통 오류 표현이며 실제 원인은 아래 사유 로그로 확인한다.

예시:

    sso_login_url_rejected reason=origin_not_allowed value_type=str

| reason | 확인할 내용 |
|---|---|
| not_string | SDK.redirect_url이 str인지. None/bytes/URL 객체 등은 거절 |
| empty | SDK 로그인 주소 생성/설정이 완료됐는지 |
| invalid_origin | 절대 http/https URL인지, host/port가 유효한지, userinfo가 없는지 |
| unsafe_characters | URL 원문에 공백·역슬래시·제어문자가 있는지. SDK 공식 URL 생성 확인 |
| origin_not_allowed | SDK URL의 scheme://netloc와 SSO_ALLOWED_ORIGINS의 정확한 일치 |

SDK URL이 `https://sso.example.internal/login?ticket=...`이라면:

```yaml
SSO_ALLOWED_ORIGINS:
  - "https://sso.example.internal"
```

이 목록에는 회사 SSO **로그인 이동 대상**의 origin을 넣는다. 우리 서비스의
SSO_PUBLIC_API_ORIGIN은 회사 로그인 후 돌아오는 API 주소, SSO_FRONTEND_ORIGIN은
인증 완료 후의 UI 주소다. 세 값의 용도를 구분한다. SSO_ALLOWED_ORIGINS에
/login 같은 경로나 끝 slash·query를 넣지 않는다. SDK URL에 포트가 있으면 해당
포트까지 일치해야 한다. 현재 비교는 정확한 문자열이며 default port/host case를
자동 정규화하지 않는다. URL 형식·미허용 대상 거절을 우회하지 않는다.

로컬 HTTP API 예시(주소는 실제 환경에 맞게 변경):

```yaml
SSO_COOKIE_SECURE: false
SSO_PUBLIC_API_ORIGIN: "http://localhost:5000"
SSO_FRONTEND_ORIGIN: "http://localhost:5000"
SSO_ALLOWED_ORIGINS:
  - "http://sso.example.internal"
```

SDK가 http origin을 반환하면 allowlist도 http여야 한다. localhost/127.0.0.1도
같은 문자열이 아니므로 브라우저 접근/API 복귀 주소를 일관되게 사용한다.

URL의 redirect_uri가 단순 API root이면 이502 검증과는 별개로 회사 로그인 후
서비스 세션이 발급되지 않을 수 있다. 복귀 주소는 `_create_sdk`에 전달되는
서버 return_url(기본 /api/v1/auth/login/sso 및 return_to/target/sso_callback query)을 사용해야
한다. 122부터 공용 SDK 요청 args의 ORIGIN에 자동 연결한다. SDK client_id/허용 복귀 주소 등록
규칙도 실제 회사 명세로 확인한다. 문자열을 이어 붙여 SDK URL을 변조하지 않는다.

SDK의 반환 **타입**, 이동 origin과 허용 설정의 일치 여부만 확인하면 된다.
SDK 전체 소스·쿠키·전체 redirect_url·ticket/query를 공유할 필요 없다. 로그에는
고정 reason과 제한된 타입 이름만 기록하며 URL/허용 목록은 기록하지 않는다.
HTTP502 detail과 인증 실패 시 Cookie/세션 미생성 정책을 유지한다.

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
