# 121 — 사내 SSO 로그인 URL 502 거절 사유 진단

## 기준과 현상

- 기준: feature/refactor-base, 81208bf.
- 작업: feature/sso-login-url-diagnostics.
- 사용자 보고: COMMON-INTERNAL_ERROR-001 / HTTP502 /
  Corporate SSO returned an invalid login URL.

SDK AttributeError 단계를 넘어 login_url 반환값을 검증하는 경로까지 진행했다.
그러나 기존 detail만으로 반환 타입·빈값·형식 오류·허용 origin 불일치를 구분할
수 없었다. 실제 SDK URL/내부망 YAML에는 접근하지 못하므로 원인 확정은 아직이다.
사용자에게 반환 타입과 이동 origin/SSO_ALLOWED_ORIGINS의 일치 여부를 요청했다.
전체 SDK 소스·쿠키·URL query/ticket 공유는 요구하지 않는다.
후속으로 사용자는 http origin과 API root를 가리키는 redirect_uri의 URL 형태를
알려줬다. 이 형태가 str이면 origin은 유효하며 http/https 또는 허용 host 불일치를
먼저 확인한다. 허용 설정 자체는 전달받지 않아 불일치 여부를 단정하지 않는다.
사용자 실제 URL/SDK 값은 소스에 저장하지 않고 가이드에는 예시 도메인만 쓴다.

## 변경과 보존

SsoRuntime._checked_login_url로 기존 검증을 모으고 고정 reason을 로그에 남긴다.
not_string/empty/invalid_origin/unsafe_characters/origin_not_allowed 및 value_type을
제공한다. 원본 URL·userinfo·query·ticket·쿠키·허용 목록은 기록하지 않는다.
기존 HTTP502 detail, 미허용 redirect 거절, Cookie/Redis 로그인 세션 미생성,
허용 SDK URL 그대로302 전달을 유지한다. 실제 검증/allowlist를 완화하지 않는다.

현재 SSO 문서에 reason별 확인사항, 회사 SSO 로그인 이동 origin과 우리 API/UI
복귀 origin의 차이, port를 포함한 정확한 비교 기준과 YAML 예시를 추가했다.
설정값을 추측해 바꾸거나 회사 SSO hostname/경로를 하드코딩하지 않는다.
HTTP 로컬 YAML 예시와 API root 복귀 URI가 로그인 callback을 대신하지 않는다는
점도 기록했다. 회사 SDK의 공식 return_url 연결은 폐쇄망 후속 확인 대상이다.
company.py·pyproject.toml·uv.lock은 변경0이다. 준비된 내부망 환경은
uv run --no-sync app.py --env local로 재시작한다.

## 검증

- 관련120회귀 통과. 신규15개는 반환 None/bytes/빈값/상대 URL/잘못된 scheme/
  IPv6/port/userinfo, 미허용 host/scheme/명시443 port, 공백/역슬래시/제어문자의
  거절 reason과 URL/ticket 미기록·리다이렉트/쿠키/세션 미생성을 검증.
- 정상 SDK URL/query가 그대로302 전달되고 ticket이 로그에 없는지 확인.
- 기존 회사 SDK 입력 호환 및 HTTP 로그인 왕복·사용자 식별 회귀를 함께 실행.
- uv run --locked --no-sync Ruff/ty/format 전체 검사 실행.
  Ruff3074·ty759는 기존 진단이며 새 오류 없음. 전체 format 검사 통과.
- git diff --check 및 보호된3개 파일 변경0 확인.

사내 SDK/실제 URL/Windows/SSO 서버 왕복은 검증하지 않았다. 이 진단을 추가했다고
사용자502가 해결됐다고 표현하지 않는다. 새 reason과 사용자 설정 확인으로
SDK 공식 주소 생성 또는 허용 설정을 수정해야 할 수 있다.

## 통합과 후속

베이스 병합·origin 게시 후 새 로그로 실제 거절 사유를 확인한다. runtime
allowlist를 우회하거나 전체 URL을 외부로 출력하지 않는다. 이전/다른 변경까지
모든 Git pull의 무충돌을 보장하지 않고 이번 의존성/SDK 연결 변경0을 유지한다.
