# 119 — 사내 SSO environ 요청 메타데이터 호환

## 기준과 문제

- 기준: feature/refactor-base, b14ba2d.
- 작업: feature/sso-environ-compat.
- 사용자 보고: URL/args 호환 후 attribute=environ.

SDK가 Flask/WSGI Request.environ에 접근하지만 FastAPI Request는 ASGI scope를
제공한다. 실제 SDK 소스는 외부에 제공되지 않는다. SDK가 읽는 키 이름만
사용자에게 선택적으로 요청했고, 표준 요청 메타데이터 매핑부터 진행했다.

## 변경

infrastructure/sso/environ.py에 ASGI→WSGI식 요청 메타데이터 변환을 분리했다.
SdkRequestView.environ은 요청별 dict 사본을 캐시한다. method/path/root_path/
query/protocol/server/client/scheme 및 HTTP_* headers를 제공한다. Content-Type/
Length는 접두사 없이 제공하고 중복 Cookie는 세미콜론, 다른 중복 헤더는 쉼표로
연결한다. WSGI path 문자열은 UTF-8 바이트를 Latin-1 문자열로 표현한다.

클라이언트 주소는 scope.client만 사용한다. forwarded 헤더를 전달은 하되
어댑터에서 신뢰해 IP/scheme을 바꾸지 않는다. 서버/port 정보가 없으면 빈값,
client 정보가 없으면 REMOTE_* 키 생략이다. 없는 주소를 만들어 넣지 않는다.
본문을 읽지 않고 wsgi.input/errors·Flask session·os.environ·scope 상태를
흉내 내지 않는다. SDK가 메타데이터 사본을 수정해도 원본 요청은 보존된다.

기준: [ASGI HTTP 명세](https://asgi.readthedocs.io/en/stable/specs/www.html),
[WSGI PEP3333](https://peps.python.org/pep-3333/).
기존 Starlette build_environ 구현도 읽었지만 deprecated/private gateway에
의존하거나 빈 본문을 진짜 본문처럼 전달하지 않는다. 전체 WSGI gateway가 아니다.

## 검증과 보존

- SDK 생성자 double이 startswith·args.to_dict/get·environ.get/index를 모두
  사용하도록 변경. HTTP 로그인 왕복 및 직원 검증·로그인 URL 생성 양쪽 검증.
- IPv4/IPv6·Unix socket·주소 누락, 실제 peer와 forwarded 헤더의 분리,
  Cookie/User-Agent/Host 및 Content headers, 요청 메타데이터 변환 검증.
- mount/root 경계·한글 경로·중복 Cookie/headers·Latin-1 헤더·사본 변경 격리,
  본문 미소비와 원본 body 조회 가능성을 검증. 신규10개, 관련98개 회귀 통과.
- Ruff 전체3074·ty759는 기존 오류이며 새 진단 없음. 초기 ty의 테스트 view
  타입 오류1개는 명시적 타입 확인으로 수정했다. 전체 format 검사 통과.
- git diff --check, company.py·pyproject.toml·uv.lock 변경0 확인.

실제 내부망 SDK/SSO 서버/Windows 왕복은 수행하지 않았다. 새 패키지 설치 없이
기존 SSO(request) 생성 코드로 적용한다. uv run --no-sync app.py --env local로
현재 내부망 설치 패키지를 유지한다. 사용자 Windows 파일은 덮어쓰지 않는다.

## 통합과 후속

베이스 병합·origin 게시 후 앱을 재시작한다. 추가 environ KeyError가 발생하면
SDK가 요구하는 키 이름만 확인해 매핑 범위를 확정한다. 인증 정보나 SDK 전체
소스 공유는 필요 없다. 실제 IP를 식별해야 한다면 프록시 신뢰 설정은 별도다.
모든 pull의 충돌 방지를 보장하지 않으며 이번 의존성/연결 파일 변경만0이다.
