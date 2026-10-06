# PostgreSQL 스키마와 적용 순서

2026-10-06 API 정리(101) 기준이다. [API 서비스 구조](api-service-layout.md), [공통 명령 Worker](agent-command-worker.md)를 함께 참고한다.

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

```bash
uv run python scripts/migrate.py --check-config
uv run python scripts/migrate.py
# 배포 YAML을 명시할 경우
uv run python scripts/migrate.py --env dev
```

launcher는 **CRUD head → Event head → checkpoint setup** 순서로 적용한다. 공통 명령 테이블이 준비된 뒤 구 이벤트를 이관하므로 Event chain만 먼저 실행하면 안 된다. 버전 테이블은 CRUD의 `alembic_version`, Event의 `ew_alembic_version`, SDK 자체 migration 테이블로 구분된다. 앱 기동은 Alembic을 자동 실행하지 않는다.

기존 배포에서는 입력을 차단하고 이전 API/Agent/Event writer를 종료한 후 적용한다. Executor 외부 작업은 장기 실행 중일 수 있으며, 원본 Redis 이벤트 보존이 필요하다. 기존 Event DB가 분리되어 있었다면 binding·Inbox·구 명령을 API DB로 옮기는 전환부터 수행한다. 새 마이그레이션이 다른 DB를 자동 탐색·복사하지는 않는다.

```sql
SELECT * FROM alembic_version;
SELECT * FROM ew_alembic_version;
SELECT kind, state, count(*) FROM agent_commands GROUP BY kind, state;
SELECT namespace, command_id, session_id, last_error
FROM agent_commands WHERE state IN ('FAILED', 'RECOVERY') ORDER BY updated_at DESC;
```

101에서 실제 삭제·기존 업무 데이터 보존·대기 명령 이관은 별도로 만든 로컬 테스트 PostgreSQL에서 검증했다. 기존 서비스 DB에 자동 적용하거나 기존 컨테이너를 재기동한 작업은 아니다.
