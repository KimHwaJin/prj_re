# 123 — SSO 미인증 callback 반복 이동 차단

## 기준과 재현

- 기준: feature/refactor-base, f5e912f.
- 작업: feature/sso-callback-loop-guard.
- 사용자 현상: 브라우저에서302가 반복되고 리다이렉트 횟수 초과 표시.

실제 회사 SDK·브라우저를 사용할 수 없으므로 SDK 미인증 double로 HTTP 왕복을
재현했다. 최초 로그인302 이후 서버가 만든 callback을 다시 호출해도302이며,
Location은 같은 회사 SSO 주소다. 서비스 쿠키는 발급되지 않는다. 따라서
직원 검증이 계속 None이면 회사 SSO↔callback이 반복될 수 있는 코드 경로가 있다.
이 재현만으로 사용자 Windows의 실제 쿠키/SDK 실패 원인을 확정하지 않는다.

## 변경과 경계

서버 callback에 sso_callback=true를 추가한다. 최초 요청의 SDK 미인증은 기존
302 이동을 유지하며, callback의 미인증은 HTTP401로 끝낸다. 직원 검증 성공은
기존 DB 사용자 연결·Redis 세션 발급·302 UI 이동을 유지한다. SDK 예외는503이다.

이 표시는 재시도 차단용이며 인증 증명·OAuth state·nonce가 아니다. 임의 표시를
붙여도 SDK 검증을 우회하지 못한다. callback query를 회사 서버가 제거하면
이 가드는 적용되지 않으므로 공식 복귀 주소 규칙과 실제 Location을 확인한다.

미인증 callback 로그에는 고정 메시지와 cookie_header_present boolean만 남긴다.
Cookie 값·전체 URL·ticket·프로필은 기록하지 않는다. False는 Cookie 헤더가
없거나 빈 경우이며, True는 회사 Cookie의 존재/유효성을 보장하지 않는다.
미인증에는 사용자 등록·Redis 세션 생성·Set-Cookie·Location이 없다.

company.py·pyproject.toml·uv.lock은 변경0이다. 사용자 Windows의 private
SSO(request) 연결과 내부망 패키지를 보존한다. SDK에 새 API나 토큰 교환 규격을
가정하지 않고 기존 직원 검증 계약을 유지한다. 공개 API에는 optional boolean
query sso_callback이 추가되며 최초 로그인 클라이언트는 기존 URL을 사용한다.

## 검증

- SDK 미인증으로 최초302 후 callback401인지 Cookie 헤더 없음/있음2개 확인.
  user bind/Redis session/Set-Cookie/Location 없음, 안전한 boolean 로그 확인.
- 직접 붙인 callback 표시는 인증 우회 불가, users/me401 확인.
- callback의 SDK 예외는503이며 비공개 예외값 로그 비노출 확인.
- HTTPS 프로젝트 및 HTTP localhost demo/docs 정상 왕복3개에서 callback
  query 보존·검증 후302·서비스 쿠키·직원정보 경계가 유지됨을 확인.
- 관련130회귀 통과. 실제 회사 SDK·서버·브라우저가 아닌 HTTP/SDK doubles다.
- uv run --locked --no-sync Ruff/ty/format 전체 검사 실행.
  Ruff3074·ty759 기존 진단 외 새 오류0, 전체 format 통과.
- git diff --check 및 보호된3개 파일 변경0 확인.

## 통합과 실제 확인

베이스에 병합하고 베이스·파생을 origin에 게시한다. 서비스 미배포 상태다.
폐쇄망에서 pull 후 uv run --no-sync app.py --env local로 재시작하고 브라우저의
새 로그인 요청으로 확인한다. callback 미인증이401로 바뀌는 것은 반복 차단이며,
실제 회사 인증 성공이 확인됐다는 뜻은 아니다.

Network Preserve log에서 반복 호스트/경로, callback의 Cookie 헤더 유무를
확인한다. 회사 도메인 쿠키는 Domain 정책상 localhost로 자동 전달되지 않는다.
사내 SDK가 ticket 처리 또는 로컬용 쿠키 생성 절차를 요구한다면 공식 계약을
확인해 연결해야 한다. 현재 회사 adapter는 Cookie 헤더 없음 또는
check_day_cookie=False일 때 None이다. 그 원인은 사용자 환경에서 추가 검증한다.
참고: [MDN Cookie Domain](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Set-Cookie#domain).
쿠키/토큰값·SDK 소스·전체 redirect_url은 공유받지 않는다.
