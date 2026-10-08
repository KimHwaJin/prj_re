# sso — 독립 FastAPI SSO 패키지

다른 FastAPI 서비스에 이식할 SSO 복사본이다. `standalone/sso` 디렉토리만
복사해서 설치할 수 있다. `dtest`·Agent·Worker·SQLAlchemy·회사 SDK 소스를
포함하거나 import하지 않는다. 현재 dtest 서비스는 이 패키지를 사용하지 않는다.
기준 버전과 추출 이후 차이는 [SOURCE.md](SOURCE.md)에 기록한다.

## 설치와 전달

프로젝트 이름과 import 이름은 모두 `sso`다. 같은 환경에 이미 동명의 패키지가
설치되어 있는지 확인하고 사내 저장소 또는 전달받은 wheel로 설치한다.
외부 PyPI의 `sso`를 이름만으로 설치하지 않는다.

디렉토리를 전달받은 서비스의 가상환경에서:

```bash
uv pip install /path/to/sso
```

개발·검증할 때는 이 디렉토리에서:

```bash
uv sync --group dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check
```

wheel을 만들고 폐쇄망에 전달할 때:

```bash
uv build --wheel --out-dir dist
uv pip install /path/to/sso-0.1.0-py3-none-any.whl
```

실행 의존성은 FastAPI·Pydantic·Redis client다. 해당 서비스에 이미 설치되어
있으면 호환 버전으로 재사용한다. SDK 및 SDK가 요구하는 사내 패키지는 폐쇄망의
기존 절차로 따로 설치한다. 폐쇄망의 wheel 의존성 준비도 필요하며, 이 저장소의
최상위 pyproject.toml/uv.lock을 가져갈 필요는 없다. 라이브러리의 uv.lock은
배포하지 않으며 설치한 서비스의 lock에서 버전을 관리한다.

## 구성과 책임

```text
src/sso/
├── settings.py          설정 모델·검증, 파일/env 자동 읽기 없음
├── contracts.py         검증 직원 정보·SDK·서비스 사용자 연결 계약
├── runtime.py           로그인·callback·세션 조회·로그아웃·설치
├── dependencies.py      로그인 확인과 변경 요청 CSRF 검증
├── errors.py            FastAPI 기본 handler가 처리하는 SSO 오류
├── storage/             Redis 인증 세션·일회성 복귀 정보
└── sdk/                 SDK 동기 호출·요청 호환·5개 직원 필드 변환
```

서비스가 할 일은 SDK 생성 연결, 사용자 연결/등록, 설정 주입, API 권한 확인,
앱 종료 처리다. 기본 프로젝트 생성·관리자 권한·사용자 DB 모델은 포함하지 않는다.

## SDK 연결 — SDK 자체 수정 없음

[examples/company_adapter.py](examples/company_adapter.py)의 `create_sdk()`에
사내 SDK의 공식 생성 코드를 넣는다. 확인된 SDK가 `SSO(request)`를 사용하면:

```python
# 실제 모듈 경로는 폐쇄망에서 정한다.
from your_private_module import SSO


def create_sdk(request, return_url):
    return SSO(request)
```

패키지는 동기 SDK 호출을 thread pool에서 실행한다. 전달되는 request는
문자열 url, args/cookies.to_dict(), environ[HTTP_HOST]를 지원한다. 로그인
URL을 만드는 시점에만 args ORIGIN을 서버가 생성한 callback으로 치환한다.
원본 ASGI 요청이나 SDK 소스를 수정하지 않는다. SDK가 반환한 redirect_url은
이어 붙이거나 재인코딩하지 않는다. SDK의 실제 HTTP 연결/읽기 timeout은 SDK가
지원하는 방식으로 별도 설정해야 한다. 패키지 timeout만으로 실행 중인 동기
thread의 네트워크 호출을 강제 종료할 수는 없다.

`create_sdk_adapter(create_sdk)`가 지원하는 계약:

- `redirect_url`: SDK가 만든 문자열 이동 주소.
- `check_day_cookie(cookie)`: True면 직원 조회, False면 미인증. 다른 값은 오류.
- `get_sso_info(cookie)`: 사번·이름·영문 이름·부서·메일 순서의 5개 tuple/list.
  사번은 문자열로 보존한다. 다른 반환 규격은 서비스에서 직접 SsoAdapter를
  구현하거나 SyncSsoAdapter에 변환 함수를 전달한다.

샘플은 SDK 연결 전 로그인503으로 종료하며 가짜 인증 성공을 제공하지 않는다.
실제 회사 Cookie·callback 허용 경로·직원 검증은 폐쇄망에서 확인한다.

## 기존 앱에 연결

```python
from sso import SsoSettings, attach_sso, create_sdk_adapter

runtime = attach_sso(
    app,  # 이미 존재하는 FastAPI 앱
    settings=SsoSettings(
        namespace="my-service:prd:sso",
        cookie_name="my_service_session",
        public_api_origin="https://api.example.internal",
        frontend_origin="https://app.example.internal",
        allowed_origins=("https://sso.example.internal",),
        allowed_return_roots=("/", "/projects"),
    ),
    users=my_user_directory,
    adapter=create_sdk_adapter(create_sdk),
    redis_url=my_redis_url,
    api_prefix="/api/v1",
    docs_path="/docs",
)
```

`UserDirectory.bind(employee) -> str`를 async로 구현하여 검증된 직원을 자체
사용자에 연결한다. 반환값은 그 서비스의 사용자 ID다. 최초 등록·비활성 사용자
거절·초기 데이터 생성은 이 함수의 정책이다. DB 작업은 짧은 transaction으로
마치고 Redis 세션 저장까지 연결을 잡고 있지 않는다. HTTPException으로 거절할
수 있으며 서비스의 기존 예외 handler가 처리한다.

직접 adapter를 넘기지 않는다면 `SsoSettings.adapter_factory`에
`module:factory`를 넣는다. factory(settings)는 async verify/login_url 메서드를
가진 SsoAdapter를 반환해야 한다. 예시는 `company_adapter:create_adapter`다.

설정은 서비스에서 YAML·env·기본값으로 읽은 뒤 SsoSettings에 주입한다. 패키지는
설정 파일을 자동 선택하거나 환경변수를 읽지 않는다. 예제 JSON은 호스트 앱의
설정 주입 방법 하나를 보여주며 JSON 사용을 강제하지 않는다.

## lifespan과 Redis 소유권

attach_sso는 기존 lifespan·middleware·예외 handler·다른 router를 덮어쓰지
않는다. 앱의 기존 lifespan 시작에 등록하고 종료 finally에서
`await runtime.close()`를 호출한다. 같은 앱에 두 번 설치하면 오류다.

기본은 redis_url로 로그인 전용 bounded pool을 만든다. Streams의 BLOCK 연결은
별도 pool을 쓰되 같은 Redis 서버·DB를 사용할 수 있다. 기존 async Redis client를
`redis=...`로 주입하면 패키지가 그 client를 닫지 않는다. 주입한 쪽이 소유한다.
Redis 연결이나 SDK client를 모듈 import 시 전역으로 만들지 않는다.

모든 replica는 같은 namespace·쿠키 이름·origin·TTL·Redis DB를 사용한다.
서비스/환경 간에는 namespace와 cookie_name을 구분한다. 이 두 값은 필수다.
공통 Redis를 쓴다고 서비스 간 로그인 세션이 자동 공유되는 것은 아니다.

## API 계약

기본 prefix `/api/v1` 기준이며 prefix는 설치할 때 변경 가능하다.

| 경로 | 입력 | 응답 |
|---|---|---|
| GET /auth/login/sso | return_to=/, target=app 또는 docs | 302 회사 SSO 또는 최종 화면 |
| GET /auth/login/sso/callback/{flow_id} | 서버 생성 일회용 path | 검증 성공302, 미검증401, 만료/재사용400 |
| GET /auth/session | 서비스 로그인 Cookie | 사용자 ID·CSRF 토큰·만료시간200, 미로그인401 |
| POST /auth/logout | 로그인 Cookie + X-CSRF-Token | 현재 서비스 세션 제거204 |

`/auth/session` 응답:

```json
{
  "user_id": "service-user-id",
  "csrf_token": "opaque-random-token",
  "expires_at": 2000000000
}
```

expires_at은 UTC Unix 초다. 직원 profile·관리자 role은 이 응답에 넣지 않는다.
GET session은 Cache-Control: no-store다. 로그인/로그아웃도 캐시하지 않는다.
로그아웃은 회사 전체 SSO 로그아웃이 아니다.

로그인은 브라우저 최상위 이동으로 시작한다. SDK callback은 다음처럼 query가
없는 주소이며 return_to/target은 Redis에 기본300초·한 번 사용으로 보관한다.

```text
https://api.example.internal/api/v1/auth/login/sso/callback/{flow_id}
```

callback에서 flow는 Lua GET+DEL로 원자적으로 소비한다. EVAL 권한이 필요하다.
flow나 callback 주소 자체는 인증 증명이 아니며 SDK 직원 검증 후에만 로그인
세션을 발급한다. callback query로 return_to/target을 변경할 수 없다.
SDK200이 정확한 생성 callback 주소를 반환하는 경우 이동만 허용한다.
회사 쪽이 callback 경로를 등록해야 한다면 동적 flow_id 경로 허용 규칙을 확인한다.

## 보호할 API와 프론트

```python
from sso import LoginDependency


@app.get("/items")
async def items(session: LoginDependency):
    return await service.list_items(session.user_id)
```

Depends를 선언한 API만 보호된다. router 전체에
`dependencies=[Depends(get_login_session)]`를 설정할 수도 있다.
`GET/HEAD/OPTIONS` 외의 보호 요청은 X-CSRF-Token을 검증한다. 프론트는
GET /auth/session으로 토큰을 받은 뒤 변경 요청 header에 넣는다.

```javascript
const response = await fetch(`${api}/api/v1/auth/session`, {
  credentials: "include"
});
const session = await response.json();
await fetch(`${api}/items`, {
  method: "POST",
  credentials: "include",
  headers: { "X-CSRF-Token": session.csrf_token }
});
```

CORS·프론트 주소·프록시 신뢰 설정은 호스트 서비스에서 관리한다. 주소 분리 시
허용 origin을 명시하고 credentials를 허용한다. 서로 다른 site라면 HTTPS 및
SameSite=None/Secure 등 실제 브라우저 정책에 맞춘 설정이 필요하다.
쿠키는 기본 HttpOnly·Secure·SameSite=lax, host-only이며 Domain을 설정하지 않는다.
로컬 HTTP 예제는 cookie_secure=false다. localhost와127.0.0.1을 혼용하지 않는다.
권한/계정 비활성 상태는 각 서비스 DB에서 확인한다. LoginDependency는 로그인
세션만 검증하며 관리자 권한이나 삭제된 사용자 여부를 대신 판단하지 않는다.

## 설정 목록

| 필드 | 기본/범위 | 의미 |
|---|---|---|
| namespace | 필수 | 서비스·환경별 Redis 키 구분 |
| cookie_name | 필수 | 서비스별 브라우저 쿠키 구분 |
| public_api_origin | 없음 | 브라우저가 복귀할 API origin, 경로 없는 주소 |
| frontend_origin | 없음 | 로그인 완료 후 화면 origin |
| allowed_origins | 빈 목록 | SDK가 반환한 회사 로그인 URL의 허용 origin |
| allowed_return_roots | / | 최종 화면 경로 허용 목록; /는 정확히 /만 허용 |
| adapter_factory | 빈 문자열 | SDK adapter 생성 함수 module:factory |
| cookie_secure | true | HTTPS 쿠키; 로컬 HTTP는 false |
| cookie_samesite | lax | lax/strict/none; none은 Secure 필요 |
| session_ttl_seconds | 1800,60~86400 | 인증 세션 TTL, 요청 때 연장하지 않음 |
| login_flow_ttl_seconds | 300,60~1800 | 임시 복귀 정보 TTL, 인증 세션과 별개 |
| redis_max_connections | 8,1~128 | 자체 로그인 연결풀 크기 |
| redis_timeout_seconds | 3초 | 자체 pool 대기·Redis 연결/명령 timeout |
| call_timeout_seconds | 10초 | SDK 호출 대기 timeout |

return roots의 `/projects`는 자신과 하위 경로를 허용한다. 절대 외부 URL·//·경로
순회·백슬래시·fragment 등을 return_to로 받지 않는다. origin은 포트까지 정확히
비교하며 끝에 /를 붙이지 않는다. 역할/가입 정책은 설정 필드에 넣지 않는다.

## 예제 실행

이 디렉토리에서 설치 후:

```bash
cd examples
uv run --project .. uvicorn app:app --host 127.0.0.1 --port 5000
```

Windows에서도 같은 명령을 사용할 수 있다. /health와 /demo는 SDK 없이 열리고,
/items 보호 API는 미로그인401이다. company_adapter.py와 config.local.json을
실제 폐쇄망 값으로 채운 후 `http://localhost:5000/demo`에서 로그인한다.
추가 설정 파일은 호스트 예제의 SSO_CONFIG 환경변수로 경로를 지정할 수 있다.
SDK 연결 전503은 정상이며 실제 사내 인증 성공을 의미하지 않는다.

실제 Redis 회귀는 명시적으로 선택한 로컬 서버의 임의 namespace만 사용한다.
운영 키나 consumer group을 변경하지 않는다.

```bash
SSO_TEST_REDIS_URL=redis://127.0.0.1:6379/0 uv run pytest tests/test_redis.py
```

PowerShell에서는 `$env:SSO_TEST_REDIS_URL = "redis://127.0.0.1:6379/0"`로
설정 후 `uv run pytest tests/test_redis.py`를 실행한다. Redis 미지정 시4개 skip이다.

## 변경 관리

이식본과 dtest 구현은 자동 동기화되지 않는다. 패키지를 수정할 때 SOURCE.md와
CHANGELOG.md에 이식본 변경을 기록하고 패키지 버전을 올려 배포한다.
SSO 프로토콜/직원 검증/보안 수정은 양쪽 적용 여부를 함께 검토한다.
SDK 원문·실제 URL query·Cookie·토큰·사내 설정을 소스나 로그에 넣지 않는다.
