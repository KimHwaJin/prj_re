# 현재 회귀 테스트

기본 `uv run python -m pytest`는 `tests/`와 `scripts/diagnostics/tests/`를 수집한다.
보고서에 복사된 과거 probe와 선택 실행 성능 workload는 기본 회귀에 포함하지 않는다.

```sh
uv run python -m pytest --collect-only -q
uv run python -m pytest -q tests/agent_service
uv run python -m pytest -q scripts/diagnostics/tests
```

pytest 수집 성공은 테스트 실행 성공과 다르다. PostgreSQL/Redis 조건을 제공하지
않으면 해당 통합 회귀는 skip된다. 회귀의 조건·DB 이름과 localhost 검사 및
초기화 범위는 각 fixture를 따른다. 실제 서비스 DB로 연결하지 않는다.

| 환경변수 | 용도 |
| --- | --- |
| DTEST_IDENTITY_TEST_DATABASE_URL | localhost의 identity_test, SQLAlchemy asyncpg URI |
| DTEST_AGENTIC_TEST_SETTINGS_FILE | database_url=agentic_runtime_test, checkpoint_db_uri=agentic_checkpoint_test를 가진 로컬 JSON |
| DTEST_SSO_TEST_REDIS_URL | 격리된 로그인 세션 Redis 회귀 |
| 기타 DTEST_* 변수 | 테스트 파일의 명시적 opt-in 조건 |

DB 회귀는 schema 초기화·TRUNCATE·migration을 수행할 수 있다. 같은 DB에서
pytest·벤치마크·서비스를 동시에 실행하지 않는다. 기업 SSO SDK·실제 LLM·운영
Executor 사용 여부도 테스트 double과 별도로 구분한다.

업무 회귀의 auth_double은 소유권·잠금을 테스트하기 위한 dependency override다.
production의 X-User-Id 인증 허용 코드가 아니다. SSO 쿠키·CSRF와 잘못된 헤더
거절은 별도 SSO 회귀에서 검증한다. migration 테스트는 지금도 실행되는 이행
코드를 검증하므로 legacy라는 이름만으로 제거하지 않는다.

벤치마크 테스트는 해당 경로를 명시해 실행한다. private capture가 필요한 경우
그 조건이 없으면 skip된다. [도구 범위](../scripts/benchmarks/README.md)를 따른다.

```sh
uv run python -m pytest -q scripts/benchmarks/executor_throughput
uv run python -m pytest -q scripts/benchmarks/process_scaling/test_scaling_analysis.py
```

과거 보고서의 test_portable_probe.py 세 사본은 삭제했다. 같은 이벤트 ID로
다른 내용을 replay해도 최초 payload와 event/log ID·sequence가 보존되는 검증은
`test_plan_event_batch_postgres.py`의 현재 회귀에 통합했다.
