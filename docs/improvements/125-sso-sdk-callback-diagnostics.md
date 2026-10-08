# 125 — SDK 복귀 주소 일치 진단

## 기준과 확인한 사실

- 기준: feature/refactor-base, 2e1070f.
- 작업: feature/sso-sdk-callback-diagnostics.
- 사용자가 본 서비스 로그: corporate_sso, callback=False, Cookie 헤더 없음.
- 사용자가 본 회사 응답 Location: 최초 요청의 /api/v1/auth/login/sso?return_to=/demo.
  서버가 생성하는 target=app 및 sso_callback=true가 없음.
- 사용자: 브라우저 query parameters 항목을 찾기 어려움.

복귀 주소의 표시 누락과 Cookie 부재로 서비스가 최초 로그인처럼 다시 SSO로
보내는 경로가 확인됐다. SDK가 처음부터 다른 주소를 생성했는지 회사 서버가
주소를 변경했는지는 아직 확인하지 못했다. 이 작업은 그 경계를 진단한다.

## 변경

SyncSsoAdapter.login_url은 기존 SDK thread pool 호출의 반환 URL을 그대로
유지하면서 query의 redirect_uri와 전달한 서버 return_url의 정확한 문자열
일치를 검사한다. 표준 parser로 query를 한 번 decoding한다. matches/differs/
missing/multiple/unreadable 고정 상태만 INFO에 기록한다.

redirect_uri는 사용자가 앞서 설명한 SDK URL에 있던 query 이름이다. 다른
이름을 쓰거나 query 외 전달 방식인 SDK는 missing으로 표시될 수 있다.
이 진단은 새 SDK 필수 계약이나 URL 유효성/인증 성공 판정이 아니다. SDK가
추가 query를 붙여도 differs일 수 있다. 실제 SDK의 공식 규격을 확인해야 한다.
URL값·호스트·ticket·쿠키·직원정보는 기록하지 않는다.

SDK 입력의 ORIGIN callback 연결·검증·반환 URL·runtime 허용 origin 정책·
401 가드는 그대로다. SDK URL을 강제로 재작성하지 않는다. company.py·
pyproject.toml·uv.lock 변경0으로 내부망 연결 및 패키지를 보존한다.

## 검증

- 실제 HTTP SDK double 왕복3개에서 callback 일치 로그·URL 비노출 확인.
- 일치/최초 URL/빈값/query없음/다른필드/중복/잘못된URL/비문자열8개 진단.
- 각 경우 SDK 반환 값을 그대로 반환하고 서버 callback은 SDK args ORIGIN 및
  별도 인자에 전달되는지 확인. private ticket/호스트/전체callback 비노출 검증.
- 관련140회귀 통과. 실제 회사 SDK·서버·브라우저 검증은 아니다.
- uv run --locked --no-sync Ruff/ty/format 전체 실행.
  추가 import 정렬1건 수정 후 Ruff3074·ty759 기존 진단 외 추가0.
  전체 format 통과. git diff --check 및 보호3개 파일 변경0 확인.

## 통합과 다음 확인

베이스 및 파생을 병합·푸시한다. 서비스 미배포이며 폐쇄망 actual SSO 인증
성공은 미확인이다. pull·앱 재시작 후 /demo 실제 로그인에서
sso_sdk_callback_binding redirect_uri 상태 한 줄을 확인한다.

matches인데 회사 Location이 다르면 회사 측 복귀 주소 처리/중간 이동을 확인한다.
differs이면 SDK로 전달한 ORIGIN/return_url과 SDK 공식 URL 생성 규칙을 확인한다.
missing이면 query 필드/POST 또는 다른 전달 계약인지 확인한다. 결과 없이
SDK나 회사 서버가 원인이라고 단정하거나 토큰 교환을 추측해 구현하지 않는다.
