# 클론 후 서비스와 /demo 실행

`app.py`가 API·공통 Agent Worker·Executor 이벤트 수신을 시작하고 `/demo`에서 같은 서버에 연결된 기능 테스트 HTML을 제공한다. HTML을 위해 별도 서버나 테스트용 JSON 설정을 만들지 않는다. 프론트 빌드와 Docker도 필수는 아니다. PostgreSQL·Redis·Executor는 기존 서버 또는 로컬 설치본에 연결한다.

## 설정 파일

레포 루트의 `.env.example`을 `.env`로 복사한 뒤 환경에 맞게 수정한다. `.env`는 Git에서 제외된다. `--local-env-file .env`를 지정해야 읽는다. 아래 계정·비밀번호·DB 이름은 예시다.

```dotenv
APP_ENV=dev
SERVER_PORT=8000
DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@127.0.0.1:5432/chat_app
CHECKPOINT_DB_URI=postgresql://USER:PASSWORD@127.0.0.1:5432/agent
REDIS_URL=redis://127.0.0.1:6379/0
EXECUTOR_BASE_URL=http://127.0.0.1:8001
EXECUTOR_SHARED_RESULT_ROOT=/absolute/path/to/executor/shared_dir
EXECUTOR_SOURCE_TYPE=INLINE
EXECUTOR_REPORT_SOURCE_TYPE=INLINE
EXECUTOR_RUNTIME_PROFILE=default
EXECUTOR_SUBMIT_ENABLED=true
MODEL_PROVIDER=openai_compatible
MODEL_NAME=YOUR_MODEL
API_BASE_URL=http://YOUR_MODEL_HOST/v1
MODEL_API_KEY=YOUR_KEY
MODEL_STRUCTURED_OUTPUT_MODE=prompt_json
AGENT_WORKER_ENABLED=true
EVENT_WORKER_ENABLED=true
```

Executor 예제8001은 Agent API8000과의 충돌을 피하기 위한 예시다. 실제 Executor가8000이면 `SERVER_PORT=18110`처럼 Agent 서비스 포트를 바꾸면 된다. Redis 비밀번호가 있으면 `redis://:PASSWORD@HOST:PORT/0`을 사용한다. 연결 주소의 특수문자가 포함된 계정/비밀번호는 URL 인코딩한다. 실행 커널과 공유 결과 폴더는 실제 Executor 설정에 맞춘다. Executor가 발행하는 Stream 이름도 `EW_EXECUTOR_EVENT_STREAM`과 일치시킨다.

`config.dev.yml > config.yml > 환경변수 > 명시적 로컬 .env > 기본값` 순으로 항목별 해석한다. 예를 들어 공통 `config.yml`의 `checkpoint_setup_on_start: false`는 `.env`의 true보다 우선한다. 사전 schema 준비를 수행하거나 선택 YAML에서 명시적으로 변경한다. `--config`를 사용하면 지정한 파일 하나만 읽는다. [중앙 설정 정본](deployment-configuration.md)을 따른다.

## DB schema와 실행

DB 두 개는 미리 생성하고 계정 권한을 준비한다. 테스트 화면이 일반 서비스 DB를 자동 생성·삭제하거나 migration하지 않는다. 서버와 같은 설정으로 사전 migration을 수행한다.

```sh
uv sync --frozen
# SERVICE_CONFIG_FILE과 APP_ENV도 서버와 같은 값을 유지한다.
uv run python - <<'PY'
import asyncio
from pathlib import Path
from alembic import command
from alembic.config import Config
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from service_settings import load_settings, configure

settings = load_settings(profile="dev", dotenv_path=Path(".env"))
configure(settings)
for ini in ("alembic.crud.ini", "alembic.ini"):
    command.upgrade(Config(ini), "head")

async def checkpoint_setup():
    async with AsyncPostgresSaver.from_conn_string(settings.agent.checkpoint_db_uri) as saver:
        await saver.setup()

asyncio.run(checkpoint_setup())
PY

uv run python app.py --env dev --local-env-file .env --check-config
uv run python app.py --env dev --local-env-file .env
```

Workflow 추천을 사용하려면 실제 embedding 모델·차원 설정과 모델 공간 HNSW index를 추가 준비한다. [등록·검색 계약](workflow-registration-and-search.md)을 따른다. 채팅 LLM 설정만으로 embedding 설정이 자동 완성되지 않는다.

## 화면과 로그인

기본 접속 주소는 `http://127.0.0.1:8000/demo`다. 포트를 바꿨다면 같은 포트로 접속한다. 루트 `/`도 `/demo`로 이동한다. 공개 HTML에는 예제 화면과 API 경로·모델 이름·실행 모드 표시만 포함하며 DB/모델/SSO 비밀정보나 실제 사용자 자료를 넣지 않는다. 실제 업무 API는 기존 쿠키·CSRF·역할·소유권 검사를 유지한다.

로그인 버튼은 `/api/v1/auth/login/sso?return_to=/demo`로 이동한다. 사내 adapter는 `SSO_ADAPTER_FACTORY=module:factory`로 연결하며 일반 서비스가 테스트 직원을 자동 설치하지 않는다. adapter가 없으면 로그인503이다. [SSO 가이드](sso-authentication.md)에 따라 설정한다. 로컬 HTTP 로그인은 `SSO_COOKIE_SECURE=false`, `SSO_PUBLIC_API_ORIGIN`과 `SSO_FRONTEND_ORIGIN`을 접속 origin으로 지정하고 `/demo`를 복귀 허용 경로에 포함한다. 운영 HTTPS 쿠키 설정은 환경별로 유지한다.

프록시 `root_path`가 있으면 API·OpenAPI·로그인 복귀에 해당 prefix를 반영한다. 예를 들어 `/service-a`라면 `/service-a/demo`로 복귀하며 SSO 복귀 허용 경로도 이에 맞춘다. Gaia 플랫폼이 만든 앱의 자체 라우터는 덮어쓰지 않으며 이 자동 `/demo` 연결은 저장소 standalone `app.py` 기준이다.

## 파일과 진단 도구

화면 정본은 `src/api_service/static/demo.html`, 공용 렌더링은 `src/api_service/web_console.py`다. HTML은 wheel package-data와 Docker의 src 복사에 포함된다. 예전 demo와 별도 index.html 사본은 제거했다. 화면 변경 후 프로세스를 재시작한다.

`scripts/diagnostics/serve_test_console.py`는 같은 HTML로 `/test-console`을 제공한다. 임시 DB·테스트 로그인·고정 응답 등 격리 검증이 필요할 때만 사용하며 일반 서비스 실행에는 필요 없다. [별도 진단 도구](../tools/test-console/README.md)를 참고한다.
