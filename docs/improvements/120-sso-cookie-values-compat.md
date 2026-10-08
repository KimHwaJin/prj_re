# 120 — 사내 SSO cookies.to_dict 호환

## 기준과 원인

- 기준: feature/refactor-base, f71c80d.
- 작업: feature/sso-cookie-values-compat.
- 사용자 로그: attribute=to_dict object_type=dict.
- 사용자 확인: SDK의 to_dict 호출은 request.args와 request.cookies 두 곳.

args는 이미118에서 호환했다. FastAPI request.cookies는 일반 dict이므로 SDK의
cookies.to_dict 호출이 실패한다. SDK에 존재하는 요청 속성을 추가로 추측하지
않고 이번에 확인된 cookies 매핑 경계만 반영한다.

## 구현과 보존

request.py의 SdkRequestValues에 읽기 전용 get/키 조회/in/반복과 복사본 to_dict를
모았다. 기존 SdkQueryArgs는 query_params.multi_items로 중복 값을 유지하고,
새 SdkCookies는 기존 Request.cookies의 파싱된 값을 그대로 복사한다.
SdkRequestView.cookies 속성으로 동기 SDK 생성·직원 검증·로그인 URL 생성에
제공한다. flat=True는 단일 값, flat=False는 새 list 사본을 반환한다.

Cookie 문자열 재파싱·URL decoding·중복 값 정책 변경을 하지 않는다. SDK가
얻은 dict/list를 바꿔도 원본 cookies/header와 다른 view는 보존된다. SDK 호출은
기존 SSO(request)를 유지하며 Flask session이나 전체 MultiDict는 구현하지 않는다.
현재 SSO 문서에 확인된 url/args/cookies/environ 입력 계약 표를 정리했다.

company.py·pyproject.toml·uv.lock은 변경0이다. 새 라이브러리 설치나 내부망
드라이버 변경은 없다. uv run --no-sync app.py --env local로 준비된 환경을
보존한다. 이번 diff 외의 과거/다른 변경까지 모든 pull의 무충돌을 보장하지 않는다.

## 검증

- to_dict/dict 오류를 쿠키가 일반 dict인 요청으로 재현한 뒤 args/cookies
  동시 호출 성공을 확인. 예외 name=to_dict, obj=원본 쿠키 dict 확인.
- 쿠키 없음·빈 문자열·기본 쿠키·%/+ 문자열·따옴표·중복 쿠키6개 경우와
  원본 파싱 결과 보존·get 기본값·없는 키·Mapping·복사본 변경 격리 검증.
- 기존 SDK 생성자 double이 url/args/cookies/environ을 모두 사용하도록 변경.
  HTTP 로그인 왕복과 verify/login_url 양쪽 경계에서 검증했다.
- 신규7개, 관련105개 회귀 통과. 실제 내부망 SDK/Windows 왕복은 별도다.
- uv run --locked --no-sync Ruff/ty/format 전체 검사 실행.
  Ruff3074·ty759 기존 진단은 남아 있고 새 오류는 없다. 전체 format 통과.
- git diff --check, 보호된3개 파일의 변경0 확인.

## 통합과 후속

검증된 변경을 베이스에 병합하고 origin에 게시한다. 실제 사내 SSO 왕복은
폐쇄망에서 앱 재시작 후 확인한다. 추가 에러가 있다면 실패 속성/키/호출 경로만
확인하면 되며 사내 SDK 전체 소스나 실제 쿠키/직원 정보 공개는 필요 없다.
