# 클론 후 서비스와 /demo 실행

`app.py`가 API·공통 Agent Worker·Executor 이벤트 수신을 시작하고 `/demo`에서 같은 서버에 연결된 기능 테스트 HTML을 제공한다. HTML을 위해 별도 서버나 테스트용 JSON 설정을 만들지 않는다. 프론트 빌드와 Docker도 필수는 아니다. PostgreSQL·Redis·Executor는 기존 서버 또는 로컬 설치본에 연결한다.

## 설정 파일과 DB schema

[앱 설정 가이드](application-configuration.md)의 선택 환경의 독립 YAML을 사용한다. `.env` 없이 시작할 수 있다. 계정·주소는 로컬 `config.yml`에서 수정한다.

```sh
uv sync --frozen
uv run python scripts/configure.py init --env local
# config.yml의 실제 연결값·SSO·공유 경로 수정
uv run python app.py --env local --check-config
uv run python scripts/migrate.py --env local --check-config
uv run python scripts/migrate.py --env local
uv run python app.py --env local
```

기존 `.env`가 있다면 init 대신 `uv run python scripts/configure.py import-env --env local --input .env`로 한 번 이전한다. 원본은 보존하고 실제 profile은 Git/이미지에서 제외한다. 운영 DB의 migration은 배포 사전 단계에서 수행하며 화면이 DB를 자동 생성·삭제하지 않는다. DB 두 개와 계정 권한, pgvector extension은 미리 준비한다.

`선택한 YAML > env > 기본값` 순이다. `--config`는 지정 파일 하나만 읽으므로 전체 설정 파일용이다. 로컬 예제의 실제 Executor 제출은 꺼져 있으므로 필요할 때 `EXECUTOR_SUBMIT_ENABLED: true`로 변경한다. API8000과 Executor8001은 예제이며 실제 포트를 맞춘다. 공유 결과 폴더와 Redis event Stream도 실제 Executor와 맞춘다.

Workflow 추천은 채팅 LLM과 별도로 embedding 모델·차원 및 HNSW index를 준비한다. [등록·검색 계약](workflow-registration-and-search.md)을 따른다.

## 화면과 로그인

기본 접속 주소는 `http://127.0.0.1:8000/demo`다. 포트를 바꿨다면 같은 포트로 접속한다. 루트 `/`도 `/demo`로 이동한다. 공개 HTML에는 예제 화면과 API 경로·모델 이름·실행 모드 표시만 포함하며 DB/모델/SSO 비밀정보나 실제 사용자 자료를 넣지 않는다. 실제 업무 API는 기존 쿠키·CSRF·역할·소유권 검사를 유지한다.

로그인 버튼은 `/api/v1/auth/login/sso?return_to=/demo`로 이동한다. 사내 adapter는 `SSO_ADAPTER_FACTORY=module:factory`로 연결하며 일반 서비스가 테스트 직원을 자동 설치하지 않는다. adapter가 없으면 로그인503이다. [SSO 가이드](sso-authentication.md)에 따라 설정한다. 로컬 HTTP 로그인은 `SSO_COOKIE_SECURE=false`, `SSO_PUBLIC_API_ORIGIN`과 `SSO_FRONTEND_ORIGIN`을 접속 origin으로 지정하고 `/demo`를 복귀 허용 경로에 포함한다. 운영 HTTPS 쿠키 설정은 환경별로 유지한다.

프록시 `root_path`가 있으면 API·OpenAPI·로그인 복귀에 해당 prefix를 반영한다. 예를 들어 `/service-a`라면 `/service-a/demo`로 복귀하며 SSO 복귀 허용 경로도 이에 맞춘다. Gaia 플랫폼이 만든 앱의 자체 라우터는 덮어쓰지 않으며 이 자동 `/demo` 연결은 저장소 standalone `app.py` 기준이다.

## 파일과 진단 도구

화면 정본은 `src/dtest/api_service/web/static/demo.html`, 공용 렌더링은 `src/dtest/api_service/web/console.py`다. HTML은 wheel package-data와 Docker의 src 복사에 포함된다. 예전 demo와 별도 index.html 사본은 제거했다. 화면 변경 후 프로세스를 재시작한다.

`scripts/diagnostics/serve_test_console.py`는 같은 HTML로 `/test-console`을 제공한다. 임시 DB·테스트 로그인·고정 응답 등 격리 검증이 필요할 때만 사용하며 일반 서비스 실행에는 필요 없다. [별도 진단 도구](../tools/test-console/README.md)를 참고한다.
