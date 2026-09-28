# 기동과 설정 — 리팩토링 첫 단계

루트 `app.py`가 `src/service_bootstrap.py`를 호출한다. 설정은 `src/service_settings.py`에서 프로세스당 한 번 확정한다. 기존 `run.py`, `uvicorn main:app --app-dir src`도 같은 bootstrap을 사용한다. 패키지 재배치 전까지 루트 `app.py`와 `src/app` 이름이 겹치므로 launcher가 `src`를 먼저 검색하게 한다.

## 실행

```sh
python app.py --check-config
python app.py --env dev
python app.py --env dev --local-env-file .env
python app.py --config /mounted/config.yml
```

`APP_ENV=dev|stg|prd` 또는 `--env`로 환경을 선택한다. `development/staging/production` 환경변수 값도 허용한다. 기본 포트는 8000이다. `--check-config`는 설정 출처와 Worker 활성 여부만 출력하고 서버나 외부 연결을 시작하지 않는다.

`config.dev.yml` 예제는 API만 기동한다. Agent 실행을 시험하려면 `agent_worker_enabled`, `task_reconciler_enabled`를 YAML에서 켜고 DB 마이그레이션을 먼저 수행한다. Executor 이벤트 수신은 `event_worker_enabled`, 실제 제출은 `executor_submit_enabled`로 각각 지정한다. API 쓰기 요청까지 차단하는 읽기 전용 모드는 아니다.

Docker 기본 CMD도 `python app.py`로 변경했다. 기존 Compose의 명시적 Uvicorn 명령과 다중 프로세스 설정은 남아 있다. 이번 단계에서 기존 컨테이너를 재기동하거나 새 이미지를 배포하지 않았다. 최종 Pod 단일 프로세스/제한된 동시 실행 구조는 다음 실행기 단계에서 적용한다.

## 우선순위

항목마다 `config.{환경}.yml > config.yml > 환경변수 > 기본값`이다. 로컬에서 명시적으로 `--local-env-file`을 제공하면 해당 파일은 환경변수보다 낮은 순위로 들어간다. `.env` 자동 탐색이나 전역 `os.environ` 변경은 하지 않는다. 로컬 dotenv는 dev에서만 허용하고 `${...}` 변수 확장을 하지 않는다.

`--config` 또는 `SERVICE_CONFIG_FILE`을 지정하면 그 파일 하나를 사용하며 공통/환경별 YAML 자동 병합은 하지 않는다. 테스트에서 `load_settings(config={}, environ={})`는 파일과 프로세스 환경에서 완전히 격리된다.

명시된 `false`, `0`은 유지한다. **YAML에 false가 있으면 환경변수 true로 바뀌지 않는다.** 환경변수를 통한 변경이 필요한 항목은 YAML에서 생략한다. 잘못된 명시값은 시작 오류이며 기본값으로 복구하지 않는다. 동일 소스의 서로 다른 별칭 값도 오류다. 설정을 바꾸려면 프로세스를 재시작한다.

```yaml
service:
  runtime:
    agent_worker_enabled: true
    task_reconciler_enabled: true
    event_worker_enabled: false
  llm:
    model_provider: mock
    model_mock_delay_ms: 200
  checkpoint:
    checkpoint_setup_on_start: false
  executor:
    executor_submit_enabled: false
```

`service` 아래 그룹은 runtime/database/checkpoint/llm/executor/events/storage/diagnostics이고, leaf key는 환경변수 이름과 같다(대소문자 무관). 플랫폼 YAML의 다른 최상위 설정은 그대로 둘 수 있다. `service` 안의 알 수 없는 key는 오류다. `service`가 없으면 문서 전체를 서비스 설정으로 해석한다. 상대 파일 경로는 기존 소비 코드의 해석을 유지하므로 배포 PV 경로에는 절대 경로를 사용한다.

## DB별 책임과 별칭

| 설정 | 소비자 | 생략 시 |
|---|---|---|
| DATABASE_URL | API SQLAlchemy / CRUD Alembic | dev의 기존 로컬 기본값; stg/prd는 명시 필수 |
| CHECKPOINT_DB_URI | Agent 실행 / API 상태 읽기 / Executor 이벤트 resume의 checkpointer | dev의 공통 로컬 기본값; stg/prd는 명시 필수 |
| EW_DATABASE_URL | 이벤트 Inbox/Outbox·binding / API Worker bridge / Worker Alembic | DATABASE_URL에서 드라이버를 psycopg용으로 정규화 |
| WORKFLOW_DATABASE_URL | Workflow catalog/history | EW_DATABASE_URL |

`AGENT_CHECKPOINT_DATABASE_URL`은 `CHECKPOINT_DB_URI`의 별칭이다. 두 이름을 서로 다른 값으로 주면 시작을 거부한다. 역할별 DB를 하나로 강제하지 않으며, 같은 checkpoint를 읽는 소비자들의 설정을 하나로 맞춘다. URL은 실제 자격증명 없이 위 표의 이름으로만 진단한다.

`EW_REDIS_URL`은 생략 시 `REDIS_URL`, `EW_EXECUTOR_BASE_URL`은 `EXECUTOR_BASE_URL`을 따른다. 명시된 잘못된 EW 설정을 다른 DB로 바꾸는 fallback은 제거했다. Workflow 저장을 끄려면 `WORKFLOW_PERSISTENCE_ENABLED=false`를 명시한다. 단순 URL 누락으로 조용히 Null 저장소가 선택되지 않는다.

`LLM_*`의 기존 모델 관련 이름은 `MODEL_*`/`API_BASE_URL`과 공통 별칭으로 처리한다. `EXECUTOR_JOBS_PATH`는 `EXECUTOR_EXECUTIONS_PATH` 별칭이다. `PHOENIX_CONFIG_PATH`로 별도 파일을 재탐색하지 않으므로 `PHOENIX_ENDPOINT/PHOENIX_API_KEY`를 중앙 YAML 또는 환경변수로 옮긴다. 아직 Agent 모델 팩터리가 사용하지 않는 API 전용 필드(예: 최대 출력 토큰)를 이번 단계에서 새 모델 기능으로 연결하지 않았다.

## 수명과 플랫폼 경계

`create_app()`은 로컬 앱을 만들고 `create_app(platform_app=..., settings=...)`은 이미 조립된 FastAPI에 붙인다. `attach_service()`는 기존 앱/라우터 lifespan을 보존하면서 Worker 시작·종료와 공유 자원 정리를 합성한다. 두 번 붙이면 오류다. startup 실패 시 시작된 형제 작업을 정리하고, 종료에서는 모든 작업에 취소를 먼저 전달한 다음 제한 시간까지 기다린다. 기존 Run 내부 취소 정체 자체는 아직 수정하지 않았다. 종료 기한 초과는 오류로 드러내며, 취소를 무시하는 Python 코루틴을 강제 종료하는 기능은 아니다.

`/health`는 기존 생존 확인, `/service/ready`는 소유한 background loop들의 생존 확인이다. loop가 죽으면 503으로 바뀐다. DB/Redis 접속이나 큐 처리 가능성을 종합 검증하는 준비 상태는 아직 아니다.

플랫폼의 앱·라우터·미들웨어·OpenAPI·계측 및 lifespan 설정이 끝난 **후**에 결합해야 한다. 이후 `GaiaService.main()`이 lifespan을 다시 덮어쓰면 안 된다. 실제 Gaia 원본이 없으므로 템플릿의 main 초기화 분리/계측 보존까지 검증한 상태는 아니다. 플랫폼 core는 수정하지 않았다.

## 마이그레이션

두 Alembic env와 로컬 bootstrap이 동일한 중앙 설정을 사용한다. 로컬 bootstrap은 YAML 우선순위까지 적용한 실제 대상이 Compose의 postgres/chat_app·agent인지 확인하고 나서만 초기화한다. 일반 서버 시작은 checkpoint DDL을 실행하지 않도록 예제에서 꺼 두었다. 기존 로컬 bootstrap은 checkpoint setup을 명시적으로 수행한다. 새 DB의 테이블 생성과 실제 업그레이드는 배포 준비 단계에서 별도 수행한다.

외부 연결 없이 SQL만 확인하려면:

```sh
PYTHONPATH=src python -m alembic -c alembic.crud.ini upgrade head --sql
PYTHONPATH=src python -m alembic -c alembic.ini upgrade head --sql
```

`worker_past`, `workflow/tools/data_io/tmp/extract_data2.py`, 독립 부하테스트 도구는 이번 서비스 설정 통합 범위에 포함하지 않는다. source/Docker 실행을 검증 대상으로 삼았으며 기존 wheel packaging 구조는 후속 패키지 분리 때 정비한다.
