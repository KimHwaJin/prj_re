# PostgreSQL 스키마와 적용 순서

2026-10-06 API 정리(101) 기준이다. [API 서비스 구조](architecture/service-layout.md), [공통 명령 Worker](agent-command-worker.md)를 함께 참고한다.

## 현재 저장소

| 저장 대상 | 연결 설정 | 준비 방법 |
|---|---|---|
| 사용자·프로젝트·세션·메시지·Run·Task·로그·이벤트 | `DATABASE_URL` | `alembic.crud.ini` |
| 실행 명령 `agent_commands` | `DATABASE_URL` | `alembic.crud.ini` |
| Workflow JSON·쿼리 embedding `workflows`, `workflow_embeddings` | `DATABASE_URL` | `alembic.crud.ini`; pgvector >=0.8.0 필요 |
| LangGraph 프로젝트 메모리 `store`, `store_migrations` | `DATABASE_URL` | CRUD migration의 SDK Store 준비; 애플리케이션 공유 Store 풀 |
| Executor 연결 `ew_bindings`, 원본 이벤트 `ew_inbox` | `EW_DATABASE_URL` | `alembic.ini`; 실행 활성 시 API와 같은 DB 정본이어야 함 |
| LangGraph checkpoint | `CHECKPOINT_DB_URI` | `AsyncPostgresSaver.setup()`; 별도 DB 가능 |

`EW_DATABASE_URL`을 생략하면 `DATABASE_URL`에서 psycopg 연결 표기를 파생한다. SQLAlchemy/psycopg는 서로 다른 드라이버·풀이지만 API·원장·Inbox는 같은 데이터베이스다. Workflow 별도 DB/저장 토글은 삭제했으며 현재 Workflow 추천 저장은 API DB에만 존재한다. checkpoint와 프로젝트 메모리는 서로 다른 SDK 저장소다.

## 이번 삭제 마이그레이션

CRUD head `20261006_0030`:

- `llm_runs`, `project_members`, `jupyter_servers` 테이블 삭제.
- `llm_run_status`, `project_member_role` enum 삭제.
- `agent_runs`의 구 전달용 10개 필드 삭제: `agent_message_id`, `interpreted_message_id`, `workflow_stage`, `request_payload`, `redis_key`, `redis_result`, `dispatched_at`, `agent_completed_at`, `redis_received_at`, `interpreted_at`.
- 현재 사용자 소유권 `projects.user_id`, 메시지·Run 결과 `agent_response`, Task·명령·현재 Workflow·Store는 유지.

Event head `ew_0003`:

- 구 pending 사용자 invocation 및 READY/FAILED 이벤트 명령을 `agent_commands`로 한 번 이관한다. 기존 command ID·event payload·namespace·실패 횟수·마지막 오류를 보존한다. 이미 공통 원장에 있는 명령은 중복 생성하지 않는다.
- 이관할 이벤트에 API 세션/Inbox가 없거나 실행 중 owner/명령이 있으면 transaction을 실패시킨다. 같은 세션의 누락된 구 명령을 새 명령 뒤로 끼워 넣는 경우도 거절한다.
- 이후 중복 `ew_commands`, 미사용 `ew_outbox`, `ew_audit` 삭제.
- 연결되지 않은 구 Workflow 저장 `workflow_catalog`, `workflow_executions`, `workflow_adaptive_history`와 `workflow_adaptive_history_view` 삭제.
- 실제 수신·중복 판별·순서 복구에 쓰는 `ew_bindings`, `ew_inbox` 유지.

CRUD autogenerate는 현재 SDK Store/checkpoint와 다른 Event chain이 관리하는 Inbox/binding/version 테이블을 ORM 미등록이라는 이유로 삭제하지 않는다. 폐기 테이블에는 이 예외를 두지 않는다.

과거 revision 파일은 **현재 DB를 새 head로 올리는 실행 가능한 이력**이므로 삭제하지 않는다. 애플리케이션에 구 모델·서비스·호환 import는 남기지 않는다. 삭제 테이블에 예전 데이터가 있다는 이유로 유지하지 않는다. downgrade는 빈 구 스키마만 재생성하며 삭제된 데이터를 복원하지 않는다.

## 적용

로컬은 config.yml, 배포는 config.dev/stg/prd.yml 하나를 선택한다. 선택·우선순위는 [앱 설정](application-configuration.md)을 따른다.

새 DB에는 초기화가 필요하다. PostgreSQL 서버와 설정에 적힌 DB 자체를 먼저
준비해야 하며 이 스크립트는 DB 생성·서버 설치를 하지 않는다. Workflow embedding
migration을 위해 서버에 pgvector >=0.8.0 확장이 설치되어 있어야 한다.
`DATABASE_URL`과 `CHECKPOINT_DB_URI`가 서로 다른 DB라면 두 대상이 모두
존재하고 해당 계정으로 접속 가능해야 한다.

```bash
uv run python scripts/migrate.py --env local --check-config
uv run python scripts/migrate.py --env local
uv run python app.py --env local
```

`--check-config`는 설정 선택만 검증하며 DB 연결이나 테이블 존재를 검사하지 않는다.
초기화는 해당 대상의 기존 Alembic revision을 head로 올리는 작업이므로 새 DB뿐
아니라 기존 DB에도 변경이 적용된다. 테이블 미준비는 일반적으로
`relation ... does not exist` 오류이며, ConnectionDoesNotExistError/연결 끊김과
구분한다. 초기화 성공이 모든 앱 연결의 정상 동작을 보장하지 않는다.

Windows에서는 초기화 launcher와 두 Alembic chain 모두 Selector Runner를
명시한다. 전역 event loop policy를 따로 지정할 필요 없다. 앱 launcher만
수정하는 것으로 Alembic의 별도 실행 루프가 변경되지는 않는다.
[108 Windows 초기화 기록](improvements/108-windows-schema-initialization.md)을 따른다.

```bash
uv run python scripts/migrate.py --check-config
uv run python scripts/migrate.py
# 배포 YAML을 명시할 경우
uv run python scripts/migrate.py --env dev
```

launcher는 **CRUD head → Event head → checkpoint setup** 순서로 적용한다. 공통 명령 테이블이 준비된 뒤 구 이벤트를 이관하므로 Event chain만 먼저 실행하면 안 된다. 버전 테이블은 CRUD의 `alembic_version`, Event의 `ew_alembic_version`, SDK 자체 migration 테이블로 구분된다. 앱은 기본적으로 Alembic을 자동 실행하지 않는다. DB_INIT_ON_START=true면
Worker/API 시작 전에 같은 준비 코드를 실행한다. [앱 시작 설정](application-configuration.md)을 따른다.

기존 배포에서는 입력을 차단하고 이전 API/Agent/Event writer를 종료한 후 적용한다. Executor 외부 작업은 장기 실행 중일 수 있으며, 원본 Redis 이벤트 보존이 필요하다. 기존 Event DB가 분리되어 있었다면 binding·Inbox·구 명령을 API DB로 옮기는 전환부터 수행한다. 새 마이그레이션이 다른 DB를 자동 탐색·복사하지는 않는다.

```sql
SELECT * FROM alembic_version;
SELECT * FROM ew_alembic_version;
SELECT kind, state, count(*) FROM agent_commands GROUP BY kind, state;
SELECT namespace, command_id, session_id, last_error
FROM agent_commands WHERE state IN ('FAILED', 'RECOVERY') ORDER BY updated_at DESC;
```

101에서 실제 삭제·기존 업무 데이터 보존·대기 명령 이관은 별도로 만든 로컬 테스트 PostgreSQL에서 검증했다. 기존 서비스 DB에 자동 적용하거나 기존 컨테이너를 재기동한 작업은 아니다.

## Windows 드라이버 연결 오류 — 2026-10-08

현재 pyproject.toml·uv.lock은 psycopg/psycopg-binary 3.3.6,
psycopg-pool 3.3.3을 사용한다. psycopg2-binary는 별도 모듈이며 이 서비스의
의존성으로 추가하지 않는다. 같은 환경에 존재하는 것만으로 충돌하지 않는다.

`connect() takes no keyword arguments`는 테이블 미준비와 별개다.
3.3.6 내부 연결 generator는 timeout 키워드가 없고 Python 호출부도 그에 맞는다.
이전 Python 파일과 새 바이너리 함수를 조합하면 이 메시지가 재현된다.
설치 메타데이터 버전만 같아도 실제 로드 파일이 정상인지까지 보장하지 않는다.
정상 3.3.6/3.3.3에서 별도 PostgreSQL 풀 연결과 앱 초기화를 검증했다.
Windows 사용자 환경의 실제 파일 혼합 여부는 아직 확정되지 않았다.

소스 반영 후 앱을 종료하고, 프로젝트 루트에서 재설치한다.

```powershell
uv sync --locked --reinstall-package psycopg --reinstall-package psycopg-binary --reinstall-package psycopg-pool
uv run python -c "import psycopg, psycopg_binary, psycopg_pool; print(psycopg.__version__, psycopg_binary.__version__, psycopg_pool.__version__); print(psycopg.__file__); print(psycopg_binary.__file__)"
uv run app.py --env local
```

출력 버전은 3.3.6 / 3.3.6 / 3.3.3이어야 한다. 패키지는 해당 사내 환경에서
사용하는 패키지 저장소·wheel을 통해 설치한다. 이전 프로세스를 반드시 종료하고
새 프로세스에서 실행한다. 드라이버 내부 함수를 monkeypatch하지 않는다.

ProgrammingError가 남으면 background_database_failure의 sqlstate를 확인한다.

| SQLSTATE | 의미 | 확인/조치 |
| --- | --- | --- |
| 42P01 | 테이블 없음 | 선택 DB·schema가 올바른지 확인 후 migration 적용 |
| 42703 | 컬럼 없음 | 코드와 선택 DB의 migration 버전 확인 |
| 42501 | 권한 부족 | 해당 계정의 DB·schema·테이블 권한 확인 |
| 42601 | SQL 문법 오류 | 실행 SQL과 지원 DB 규격 확인 |

새 DB에 migration을 적용하려면 위 수동 scripts/migrate.py를 사용하거나,
config.yml의 DB_INIT_ON_START를 true로 설정한다. YAML에 false가 있으면 환경변수
true가 우선하지 않는다. 초기화는 기본 false 정책을 유지하며 기존 migration을
적용한다. 다른 DB·잘못된 schema를 바라보는 문제는 드라이버 재설치로 해결되지 않는다.

진단 로그는 원인 예외 타입·SQLSTATE·조치 힌트만 기록하고 SQL·파라미터·접속
문자열·원문 예외 메시지는 기록하지 않는다. PoolTimeout은 연결 준비 실패의
후속 증상일 수 있으므로 앞서 나온 psycopg.pool 경고를 함께 확인한다.
