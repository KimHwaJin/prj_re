# 118 — 사내 SSO 쿼리 args 호환

## 기준과 문제

- 기준: feature/refactor-base, 30fe3de.
- 작업: feature/sso-query-args-compat.
- 사용자 보고: URL 호환 적용 후 attribute=args object_type=Request.

SDK는 Flask식 request.args를 읽지만 FastAPI는 query_params를 제공한다.
사용자가 앞서 설명한 SDK의 ORIGIN in request.args.to_dict() 접근과도 연결된다.
117의 요청 view가 없는 속성을 원본 Request로 위임하므로 로그의 객체 타입은
Request로 나타난다. 사내 SDK 전체 소스·실제 네트워크 왕복은 접근하지 않았다.

## 변경과 보존

SdkRequestView.args는 원본 query_params에서 만든 읽기 전용 SdkQueryArgs다.
Mapping get/키 조회/in/반복 및 to_dict(flat=True/False)를 지원한다. Flask
MultiDict처럼 중복 키의 단일 조회는 첫 값이다. flat=False는 모든 값을 새
list로 반환한다. dict/list 변경은 원본 요청이나 후속 조회에 반영되지 않는다.
빈값과 기존 Starlette 파싱/URL decoding을 유지하고 ORIGIN 값도 변조하지 않는다.

URL 문자열 view, 원본 Cookie·query_params·scope, 서버 callback 인자,
로그인·직원 검증·비밀값 로그 제외 정책은 유지한다. Flask session과 전체
Flask Request/MultiDict를 재현하지 않는다. 확인하지 않은 SDK 접근은 별도다.
company.py·pyproject.toml·uv.lock은 변경0이고 패키지 설치도 없다.
기존 SSO(request) 연결 코드 그대로 사용한다. Windows 앱 재시작은
uv run --no-sync app.py --env local로 준비된 내부망 환경을 보존한다.

## 검증

- SDK 생성자 double이 url.startswith와 args.to_dict/get을 함께 사용하도록
  변경해 HTTP 로그인 왕복 및 verify/login_url 양쪽 SDK 경계를 검증.
- args의 ORIGIN 조회와 서버 callback 별도 인자, 원본 Request 미변경 확인.
- 빈 쿼리·빈값·한글/URL decoding·중복 키의 첫 값/전체 값·get 기본값·
  없는 키 KeyError·Mapping 변환·복사본 변경 격리를 검증.
- 관련88개 회귀 통과. 실제 내부망 SDK/Windows 로그인 왕복은 별도 확인.
- Ruff/ty 전체 검사 실행, 기존3074/759 진단 외 새 오류 없음.
  최초 검사는 새 slots 정렬1·테스트 타입2 오류를 발견해 수정했다.
- 전체 Ruff format 및 git diff --check 통과. 의존성/SDK 연결 파일 변경0 확인.

## 통합과 후속

베이스 병합·origin 게시 후 폐쇄망에서 pull/재시작한다. 사용자 사내 SDK
연결·의존성 파일은 수정하지 않는다. 이 변경 외의 과거 수정/원격 변경까지
모든 Git pull의 무충돌을 보장하지 않는다. 다음 추가 SDK 오류가 나오면
attribute/object_type으로 실제 요구 속성을 확인한다.
