# 117 — 사내 SSO 요청 URL 문자열 호환

## 기준과 문제

- 기준: feature/refactor-base, 3ac561c.
- 작업: feature/sso-request-url-compat.
- 사용자 로그: attribute=startswith object_type=URL.
- 사용자 확인: 사내 SDK 생성 코드 SSO(request)에서 오류 발생.

FastAPI Request.url은 Starlette URL 객체다. Flask식 문자열 URL을 기대하는
SDK 생성자의 startswith 호출과 호환되지 않는다. 사내 SDK 소스는 제공받지
않았으며 SDK 내부 전체 구현이나 다른 요청 속성의 호환성을 확정하지 않는다.

## 변경과 내부망 보존

infrastructure/sso/request.py에 SdkRequestView를 추가했다. url만 str로
제공하고 다른 속성 읽기는 원본 FastAPI Request에 위임한다. SyncSsoAdapter가
verify/login_url 동기 함수를 thread pool에 넘길 때 적용한다. async 어댑터나
다른 API의 요청 객체, 원본 Request.url은 수정하지 않는다.

기존 private factory의 Request 주석을 유지하기 위해 SDK 경계에서만 cast를
사용한다. 런타임 view는 Request 인스턴스가 아니다. Cookie 헤더·query_params·
ORIGIN·scope·method를 그대로 위임한다. args/to_dict나 Flask 세션을 재구현하지
않고, 복귀 URL은 기존 서버가 정한 별도 인자를 유지한다.

company.py·pyproject.toml·uv.lock은 변경0이다. 폐쇄망에서 작성한 SSO(request)
연결 코드를 그대로 사용할 수 있으며 별도 패키지 설치는 없다. 현재 환경을
보존하려면 uv run --no-sync app.py --env local로 실행한다. 이전 미반영 변경·
다른 개발자의 의존성 변경까지 모든 pull의 무충돌을 보장하지 않는다.

## 검증

- raw FastAPI URL에 startswith를 쓰는 SDK 생성자 double로 동일 AttributeError
  (name=startswith, obj=원본URL)를 재현한 뒤 요청 view 적용 성공을 검증.
- 직원 검증·로그인 URL 생성 양쪽 경계의 문자열 URL과 Cookie·query·scope·
  method 위임, 서버 callback 유지, 원본 URL 객체 미변경 확인.
- HTTP SSO 로그인 왕복 테스트도 문자열 URL을 요구하는 생성자로 검증.
- 관련83개 회귀 통과. 실제 회사 SDK/Windows/SSO 서버 연결은 별도다.
- Ruff 전체3074·ty759 기존 오류는 남아 있다. 새 진단 없음.
  uv run --locked --no-sync로 기존 환경을 사용했다. 전체 format 검사 통과.
- git diff --check 및 보호된3개 파일의 변경0 확인.

## 통합과 남은 확인

사용자 요청한 SSO 수정 작업을 이어서 베이스 병합·origin 게시한다. 운영 배포는
하지 않는다. 폐쇄망에서는 pull 후 앱을 재시작해 실제 SDK 생성이 진행되는지
확인한다. 추가 AttributeError가 있으면 새 attribute/object_type을 기준으로
다음 경계를 확인한다. 현재 수정으로 SDK 전체 호환이 검증됐다고 표현하지 않는다.
