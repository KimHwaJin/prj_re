# 122 — SDK ORIGIN에 서버 로그인 복귀 URL 연결

## 기준과 문제

- 기준: feature/refactor-base, 97266fd.
- 작업: feature/sso-return-url-binding.
- 사용자 확인: URL 허용 설정 후302로 로그인 시도는 진행됨. return_url을
  어디에 어떻게 넣는지 요청. 앞서 SDK는 args.to_dict의 ORIGIN을 읽는다고 설명.

runtime은 서버 callback을 이미 만들고 private factory 두 번째 인자로 전달했다.
하지만 SSO(request)만 호출하는 private factory에서는 SDK가 그 인자를 받지
않는다. SDK 기본 redirect_uri가 API root라면 서비스 로그인 callback 경로를
거치지 않는다. 302는 정상 이동 상태이며 Location으로 진행 단계를 구분해야 한다.

## 변경

SyncSsoAdapter.login_url에서 sdk_request(request, return_url)을 사용한다.
SdkRequestView는 로그인 URL 생성에만 args의 기존 ORIGIN들을 모두 제거하고
서버가 생성한 callback 하나를 넣는다. SDK는 기존 ORIGIN 조회/공식 redirect_url
생성 방식으로 복귀 주소를 사용한다. URL을 직접 이어 붙이거나 재작성하지 않는다.

verify의 return_url은 None이므로 args를 바꾸지 않는다. 원본 ASGI query_params/
scope/header/cookie와 다른 query 값·중복은 보존한다. 서버 callback은 기존
SSO_PUBLIC_API_ORIGIN/API_V1_PREFIX 및 검증된 return_to/target으로 생성하며
사용자 ORIGIN·Host·forwarded 값으로 만들지 않는다.

company.py·pyproject.toml·uv.lock은 변경0이다. private SSO(request) 코드와
내부망 패키지를 유지한다. 새 SDK API/클래스/패키지를 가정하지 않는다.
현재 SSO 가이드에 설정 위치·SDK 입력 연결 위치·API callback 예시와
302 Location/서비스 쿠키/users-me 확인 순서를 기록했다.

## 검증

- SDK double이 별도 return_url 인자를 사용하지 않고 args ORIGIN만으로
  redirect_uri를 생성하도록 변경해 실제 사용자 연결 패턴의 경계를 검증.
- HTTPS 프로젝트 복귀 및 HTTP localhost:5000의 /demo·/docs 복귀3개 HTTP 왕복.
  SDK redirect_uri가 callback 경로/query를 유지하고 최종302와 Cookie/직원
  프로필/로그인 조회가 정상인지 확인. 실제 회사 로그인 서버는 double이다.
- 없는/임의/중복/빈 ORIGIN4개 경우를 검증. SDK용 ORIGIN은 서버 callback 하나,
  다른 중복 query·인코딩 값·원본 요청은 유지, verify args 미변경 확인.
- 관련126개 회귀 통과. 기존120에 HTTP 왕복 추가2 및 새 query4개다.
- uv run --locked --no-sync Ruff/ty/format 전체 검사 실행.
  Ruff3074·ty759 기존 진단 외 새 오류 없음, 전체 format 통과.
- git diff --check 및 보호된3개 파일 변경0 확인.

## 통합과 후속

베이스 병합·origin 게시 후 uv run --no-sync app.py --env local로 재시작한다.
실제 SDK가 ORIGIN 전체 callback을 redirect_uri에 반영하는지, 회사 client_id/
허용 URI 등록과 쿠키 왕복은 폐쇄망에서 확인해야 한다. 실제 회사 인증 성공을
이번 mock 검증만으로 주장하지 않는다. 새 반복302가 있다면 Location이 회사
SSO인지 최종 UI인지 및 회사 Cookie 검증 결과를 확인한다. 인증값은 공유하지 않는다.
