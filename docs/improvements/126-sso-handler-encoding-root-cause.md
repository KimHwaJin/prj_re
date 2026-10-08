# 126 — SDK handler 복귀 주소 인코딩 원인 정정

## 기준과 사용자 확인

- 기준: feature/refactor-base, b3a2538.
- 작업: feature/sso-callback-encoding-review.
- SDK: ORIGIN이 있으면 __target_url로 사용, 없으면 protocol/HTTP_HOST 기본값.
- handler 문자열: URL 값에 __target_url을 그대로 삽입.
- 요청: requests.get(handler_url, params=params, cookies=..., headers=...).
- handler401: redirect_url=응답 JSON의 로그인 url.
- handler200: uuid 저장, redirect_url=__target_url.

회사 응답 Location은 callback 표시 없는 최초 로그인 주소이며, 서비스에는
Cookie가 없고 callback=False라 다시 회사 SSO로 보낸다는 사용자 관찰과 연결된다.

## 원인 재현과 정확한 범위

requests.Request.prepare를 사용하여 실제 네트워크 접속 없이 호출 URL을
검사했다. __target_url은 return_to/target/sso_callback query를 갖는 서버 callback이다.

- 기존 handler 문자열 + 추가 params: handler query는 URL/target/sso_callback/
  기존params이며, handler가 URL 값으로 받는 callback query는 return_to만 남음.
- URL도 params로 전달: handler query는 URL/기존params만 있으며, URL 안의
  callback query는 return_to/target/sso_callback이 모두 남고 원본과 일치함.

이로써 **제공된 handler 요청 생성 방식의 복귀 주소 query 분리 원인**을 재현했다.
외부 회사 서버·브라우저·실제 사내 SDK 인증은 실행하지 않았으므로 전체 로그인
실패 원인을 모두 해결하거나 Cookie 발급 성공을 확인했다고 말하지 않는다.

## 공개 레포 정리와 SDK 수정 위치

125의 SDK 반환 URL 직접 비교는 실제401/200 분기를 고려하지 않은 제한된
진단이다. 코드·로그·전용8개 테스트 및 왕복3개에 추가한 비교 assert를 삭제했다.
공개 adapter 및 company 회귀 파일은125 이전 구현으로 복원했다. 기존 SDK용
ORIGIN 연결·입력 호환·thread pool·인증·URL allowlist·callback401 가드는 유지한다.
124의302 분기 로그도 유지한다. 현재 SSO 가이드는 실제 계약으로 정정했다.

실제 수정 위치는 **폐쇄망 SDK의 handler 요청 생성부**다. URL 값도 requests
params에 넣거나 handler 문자열에서 URL 값을 urlencode해야 한다. 기존
params/cookies/headers와 target 원본은 유지한다. 현재 가이드에 두 대안과
params dict 가정을 명시했다. SDK 소스는 외부 레포에 없으므로 여기에 수정했다고
주장하지 않는다. company.py·pyproject.toml·uv.lock은 변경0이다.

## 검증

- requests PreparedRequest로 기존 실패/params 수정 두 경우를 대조함.
- 관련132회귀 통과.125에 추가한 전용8개 제거로140에서132로 변경됨.
- uv run --locked --no-sync Ruff/ty/format 전체 실행.
  Ruff3074·ty759 기존 진단 외 추가0, 전체 format 통과.
- git diff --check 및 보호3개 파일 변경0 확인.

## 통합과 남은 확인

정정·정리 작업은 베이스와 파생에 병합·푸시한다. 서비스 및 SDK는 미배포다.
폐쇄망 SDK 수정 후 handler URL 값의 callback query 보존, 실제 회사 복귀,
미인증 callback401 또는 직원 검증 후 서비스 쿠키·users/me200을 확인한다.
SDK를 수정할 수 없으면 SDK 관리자에게 해당 요청 생성부 수정·버전 배포를 요청한다.
인증 증명을 uuid 또는 redirect_url 값만으로 대체하지 않는다.
