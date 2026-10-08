# 124 — SSO302 이동 분기 진단

## 기준과 현상

- 기준: feature/refactor-base, 032e124.
- 작업: feature/sso-redirect-decision-diagnostics.
- 사용자: 확인용 sso_callback=true URL은401이지만 /demo 실제 로그인은 계속
  브라우저의 리다이렉트 횟수 초과로 끝남. 기존 앱 로그에서는302만 확인됨.
- 추가 확인: 우리 로그인302 Found와 회사 페이지302 Moved Temporarily 사이를
  반복함. 두 문구는 같은302 상태이며 표현 차이를 원인으로 보지 않는다.

확인용 URL401은 최신 callback 가드가 동작한다는 증거이며 실제 회사 SSO 왕복
실패 원인을 증명하지 않는다. 실제 복귀 query가 없어 가드를 거치지 않는 경우,
회사 SSO 서버 안에서 반복되는 경우, 서비스 인증 성공 이후의 반복 등은 기존
접속 로그만으로 구분할 수 없다. 사용자 Network의 호스트/경로 확인이 필요하다.

## 변경

SsoRuntime.login의302 반환 지점에 INFO 분기 로그를 추가했다.

- destination=corporate_sso: SDK 검증 None, 회사 SSO로 이동.
- destination=application: 직원 검증 및 서비스 세션 발급 완료, UI/docs로 이동.
- callback: 해석된 sso_callback boolean.
- cookie_header_present: Cookie 헤더가 비어 있지 않은지 boolean. 유효성 아님.

URL·query/ticket·cookie 값·직원 정보는 로그에 넣지 않는다. SDK 입력, URL
검증, 인증, 세션, callback 가드 동작은 바꾸지 않는다. 새 설정도 만들지 않는다.
기본 app.py는 INFO를 출력한다. 플랫폼 로거를 쓰면 해당 auth logger 필터를 확인한다.
공개302는 정상 흐름이므로 WARNING이 아니라 INFO로 기록한다.

company.py·pyproject.toml·uv.lock 변경0. 내부망 SDK 연결 및 패키지를 보존한다.
회사 SDK에 새로운 토큰 교환·쿠키 생성 규격을 가정하지 않는다.

## 검증

- 실제 HTTP 로그인에서 미인증/인증 성공 두302 분기 로그를 확인.
- SDK로 들어간 ticket·Cookie 및 직원 ID/이름, 반환 Location의 로그 비노출 확인.
- 정상 HTTP SSO 왕복·callback401 가드·SDK503·사용자 경계 포함132회귀 통과.
- uv run --locked --no-sync Ruff/ty/format 전체 실행.
  Ruff3074·ty759 기존 진단 유지, 추가 진단0. 전체 format 통과.
- git diff --check 및 보호3개 파일 변경0 확인.

## 통합과 실제 확인

베이스·파생을 병합/푸시한다. 서비스 미배포, 실제 회사 서버 검증은 미완료다.
폐쇄망에서 pull·앱 재시작 후 /demo 로그인 버튼으로 실제 회사 왕복을 진행한다.
앱 터미널의 sso_login_redirect 두세 줄에서 반복 분기를 확인한다.

corporate_sso/False가 회사 왕복 후에도 반복되면 Network에서 callback의 표시
보존을 확인한다. application이 반복되면 실제 UI/docs 목적지와 서비스 쿠키·
users/me를 확인한다. 최초 회사 이동 로그 이후 서비스 복귀가 없다면 Network의
회사 측 반복 경로를 확인한다. 로그 결과 없이 어느 경우인지 단정하지 않는다.
쿠키/토큰값·전체URL·사내SDK 소스는 공유받지 않는다.
