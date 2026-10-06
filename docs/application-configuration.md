# YAML 중심 애플리케이션 설정

097부터 일반 서비스의 필수 연결 설정과 동작 정책을 YAML에서 관리한다. API·Agent·공통 명령 Worker·Executor 이벤트 수신·SSO·migration은 `src/service_settings.py`의 같은 snapshot을 사용한다. 개별 컴포넌트가 별도 `.env`를 읽지 않는다. [설정 항목별 위치 목록](application-settings-inventory.md)과 [변경·검증 기록](improvements/097-yaml-application-settings.md)을 참고한다.

## 파일의 책임

| 파일 | 관리 내용 | Git / 이미지 |
|---|---|---|
| config.yml | Worker 활성·동시성, DB/checkpoint pool, Agent/HITL/메모리 정책, Executor HTTP/실행 제한, 이벤트 수신, SSE, 추천 검색 예산 | 포함 |
| config.dev.example.yml / config.stg.example.yml / config.prd.example.yml | 환경별 연결·모델·SSO·공유 경로 예제와 주석 | 포함, 그대로 접속하는 설정은 아님 |
| config.dev.yml / config.stg.yml / config.prd.yml | 실제 DB·Redis·Executor·LLM·SSO·embedding·Phoenix 값과 환경별 정책 override | 제외, 실행 시 로컬 파일 또는 마운트 |
| .env.example / 선택적 .env | APP_ENV 선택 또는 YAML에 없는 항목의 보조 env 예제 | example만 포함; 일반 실행에 .env 불필요 |
| workspace/config.compose.yml | 로컬 helper가 선택 YAML에서 생성한 앱 설정; DB host만 Compose 내부로 변경 | 제외, readonly 마운트 |
| .env.local | LOCAL_API_PORT / LOCAL_POSTGRES_PORT / LOCAL_REDIS_PORT / LOCAL_POSTGRES_PASSWORD / LOCAL_CONFIG_GID / LOCAL_APP_ENV / LOCAL_SHARED_INPUT_ROOT 등 Compose 인프라 값 | 제외; 앱 env_file로 사용하지 않음 |
| config.cicd.dev.yml | 사내 CICD에서 수정 가능했던 앱 env를 옮긴 공개 정책·주소 profile | 포함; 해당 Dockerfile에서 config.dev.yml로 설치 |
| config.diagnostic.yml | 격리된 기존 benchmark의 env 기반 fixture 선택 | 포함; 일반 서버 설정으로 사용하지 않음 |

실제 profile 파일이 없으면 시작을 거부하고 초기화 방법을 안내한다. `.env`만 있는 상태로 예전 DB/모델 기본값에 조용히 접속하지 않는다. 기존에 작성한 개인 YAML은 삭제·덮어쓰지 않는다. 이전에 추적하던 비어 있는 dev/stg/prd 파일은 example로 대체했다.

## 새 클론에서 실행

저장소 루트에서 실행한다.

```sh
uv sync --frozen
uv run python scripts/configure.py init --env dev
# 생성한 config.dev.yml에서 실제 DB/Redis/LLM/Executor/SSO 연결 값을 수정
uv run python app.py --env dev --check-config
uv run python scripts/migrate.py --env dev --check-config
# 대상 DB와 권한을 확인하고 아래 명령으로 schema 준비
uv run python scripts/migrate.py --env dev
uv run python app.py --env dev
```

기본 화면은 `http://127.0.0.1:8000/demo`, Swagger는 `/docs`다. PostgreSQL에는 `chat_app`, `agent` DB와 사용 계정·권한이 미리 필요하고 Workflow migration에는 pgvector>=0.8.0 서버 extension이 필요하다. 일반 migration은 DB 서버/DB/계정을 생성하지 않으며 데이터 reset도 하지 않는다. CRUD·이벤트 schema→checkpoint SDK setup→공통 명령 backfill 순으로 준비한다. 운영에서는 실행 중 writer를 정리하고 배포 사전 단계에서 직렬로 수행한다.

`stg`, `prd`도 해당 `init --env`로 준비한다. API의 host/port와 SSO API/프론트 origin을 함께 맞춘다. DB URL의 계정·비밀번호 특수문자는 URL 인코딩한다. 공유 저장소는 실제 Executor PVC/폴더의 절대 경로를 쓴다. `--check-config`는 외부 접속 없이 설정 출처·활성값·pool 예산을 보여주며 실제 연결 가능 여부를 보장하지 않는다.

개발 예제는 실제 Executor 제출을 꺼 두었으므로 연계 시 `executor_submit_enabled: true`로 변경한다. 이벤트 수신 활성 여부와 별개다. 예제는 `INLINE` 제출과 operation timeout/wait 300초를 사용한다. 초장기 Operation의 실행 기한은 분석 정책에 맞게 별도로 조정한다. Run 동시성은 1이고 변경·측정 없이 운영 용량을 확정하지 않는다. 사내 CICD는 기존 PATH/MANIFEST·port5000 설정을 그대로 유지한다.

실제 SSO adapter factory가 없으면 로그인은503이다. 일반 서비스는 테스트 직원을 자동 설치하지 않는다. [SSO](sso-authentication.md), [로컬 화면](service-demo-console.md)을 따른다. 채팅 LLM만 설정한다고 임베딩·Phoenix가 자동 연결되지는 않는다. embedding endpoint/model/dimensions는 함께 지정하고 [모델 공간 index](workflow-registration-and-search.md)를 준비한다.

## 기존 .env를 한 번 이전

```sh
uv run python scripts/configure.py import-env --env dev --input .env
```

인식한 앱 설정과 별칭만 중앙 loader로 검증하고 YAML 정식 이름으로 기록한다. 기존 파일은 수정하지 않으며, 공통 정책·profile 예제 위에 `.env`에 명시한 앱 값을 덮어 합친 **완성된 private YAML**을 생성한다. legacy 파일의 false/0과 실제 연결값이 공통값에 가려지지 않게 이전한다. `${...}`를 자동 확장하지 않는다. PYTHONUTF8 등 프로세스 값은 앱 YAML에 넣지 않는다.

대상 파일이 이미 있으면 덮어쓰지 않는다. 비교용 출력은 `--output /private/path/config.active.yml`, 의도한 교체는 `--overwrite`로 명시한다. 생성 파일은0600으로 원자 저장한다. 원본 `.env`는 삭제하지 않으며, 검증 후 더 이상 실행 옵션에 넘기지 않으면 된다.

폐기된 EW_DISPATCH_CONCURRENCY, EW_COMMAND_STREAM_NAME, EW_COMMAND_GROUP_NAME, EW_PUBLISH_LEASE_SECONDS 등은 오류로 알려준다. 원본에서 제거·정리하고 다시 이전한다. 활성 Worker의 EW_DATABASE_URL이 API DATABASE_URL과 다른 DB이면 오류다. 이전 도구가 DB 주소나 데이터 자체를 임의로 합치지 않는다. 예전 분리 DB 이행은 [공통 Worker](agent-command-worker.md)의 절차가 필요하다.

## 우선순위와 설정 주입

일반 선택은 `config.<환경>.yml > config.yml > 프로세스 env > 명시적 dev dotenv > 코드 기본값`이다. APP_ENV 또는 `--env`는 파일 선택 입력이다. 기본 profile은 dev다. 공통 YAML의 leaf도 env보다 우선하므로 **env로 바꿀 항목은 공통/환경 YAML 양쪽에서 해당 leaf를 생략**한다. false/0은 생략이 아니다. 설정을 바꾸면 프로세스를 재시작한다.

그룹은 문서화를 위한 분류이고 leaf 이름은 기존 대문자 env 이름의 소문자 표현이다. `${...}` 치환을 하지 않으므로 다음처럼 비밀값을 참조 문자열로 넣지 않는다. Secret env를 쓰려면 YAML의 `model_api_key` 항목을 지우고 MODEL_API_KEY를 주입한다. MODEL_CATALOG 안에 직접 key를 넣었다면 그 모델 정의도 정리한다.

```yaml
service:
  runtime:
    agent_worker_concurrency: 4
  executor:
    executor_submit_enabled: true
  llm:
    model_name: YOUR_MODEL
    api_base_url: http://YOUR_MODEL_HOST/v1
    # model_api_key를 생략하고 Secret env MODEL_API_KEY를 사용 가능
```

`--config /mounted/config.active.yml` 또는 SERVICE_CONFIG_FILE은 **파일 하나만** 읽는다. 공통+profile 자동 병합이 없으므로 전체 설정을 포함한 파일에 사용한다. example/profile은 일반적으로 `--env`로 선택한다. 플랫폼 logger/Gaia 설정은 최상위 다른 영역에 유지할 수 있고 서비스는 `service` 아래만 해석한다. service 안의 unknown key·잘못된 타입·한 소스의 충돌 별칭은 시작 오류다.

## 배포별 적용

- 로컬 Python: profile만 준비한 뒤 app.py. Docker 필수 아님.
- 로컬 Compose: `uv run python scripts/local.py up --env dev`. 같은 loader로 해석해 workspace/config.compose.yml을 생성하고 마운트한다. .env.local은 인프라 전용이다. [로컬 가이드](local-docker.md).
- 외부 인프라 Compose: config.stg.yml을 준비하고 아래의 마운트 권한을 적용한 뒤 실행한다. 선택 profile을 readonly 마운트하고 이미지의 공통 config.yml과 병합한다. host는0.0.0.0이어야 한다. Compose 포트/바인드 마운트 치환용 입력은 인프라 값으로 별도 유지한다.
- 범용 Kubernetes: 실제 profile을 Secret의 config.prd.yml 키에 넣어 파일 마운트한다. APP_ENV=prd만 selector로 주입한다. [배포 예제](../deploy/README.md).
- 사내 CICD: config.cicd.dev.yml을 이미지 config.dev.yml로 설치한다. `[설정 값 변경 불가]` env/이미지/replica/자원은 유지했다. DB·Redis·모델 key·checkpoint·EW DB 고정 env는 YAML에 같은 leaf를 넣지 않아 계속 주입된다. 실제 EW DB가 API DB와 같은지 플랫폼 담당자가 확인해야 한다. 기능 정책 변경은 YAML을 수정한다.
- 기존 부하테스트/진단 fixture는 명시적 config.diagnostic.yml 또는 격리 mapping으로 env 제어를 유지한다. 정상 배포의 공통 정책을 테스트 dotenv로 덮어쓰는 경로로 사용하지 않는다.

로컬 Python용 private profile은0600으로 생성한다. Docker bind mount는 host 파일 권한을 유지하므로 비루트 UID10001이 읽을 그룹을 지정해야 한다. 로컬 helper는 생성본만0640으로 만들고 해당 GID를 LOCAL_CONFIG_GID로 전달하여 컨테이너의 보조 그룹에 추가한다. 원본 profile과 .env.local은0600을 유지한다. 생성본의 그룹 구성원은 연결 정보를 읽을 수 있으므로 실행 계정 전용 그룹을 쓰는 환경이라면 생성 폴더의 그룹도 맞춘다.

외부 Compose는 실제 선택 profile에 그룹 읽기를 허용하고 같은 그룹 ID를 전달한다. 아래 예시는 현재 사용자 그룹을 사용한다. 별도 운영 그룹이면 파일 그룹과 APP_CONFIG_GID를 그 그룹으로 맞춘다.

```sh
chmod 640 config.stg.yml
APP_ENV=stg APP_CONFIG_GID=$(id -g) docker compose -f compose.external.yaml up --build
```

Kubernetes Secret은 일반 Secret volume 파일 권한으로 마운트되어 비루트 서비스가 읽는다. 파일 권한과 Secret 접근 권한을 실제 플랫폼 정책에 맞춘다.

개인 profile과 생성 설정은 .gitignore·.dockerignore에서 제외한다. tracked 파일에 실제 credential을 넣지 않는다. 초기화 도구가 예제 placeholder를 실제 서비스 값으로 바꾸거나 사내 SDK를 설치하지 않는다. 이번 단계는 설정 파일·실행 코드·문서 정리이며 실제 서버 재기동·DB migration·Kubernetes 적용은 별도다.
