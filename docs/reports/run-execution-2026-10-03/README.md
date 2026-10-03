# Run 실행 경계 검증 — 2026-10-03

[059 개선 기록](../../improvements/059-run-execution-boundaries.md), [개발 인수인계](../../run-execution-architecture.md). 브랜치 `feature/run-execution-boundaries`, 기준 `c5abebd`.

## 결과

| 검증 | 결과 | 원본 |
|---|---|---|
| API·Agent 전체 회귀(외부 테스트 설정 없음) | 595 passed / 342 skipped / 74 warnings / 26.12초 | [unit-regression.txt](unit-regression.txt) |
| 선택한 실제 PostgreSQL 실행·checkpoint 회귀 | 84 passed / 0 skipped / 3 warnings / 149.02초 | [postgres-regression.txt](postgres-regression.txt) |
| 결과 반영 함수 최종 가독성 정리 후 관련 DB 재확인 | 3 passed / 14.29초 | [projection-final.txt](projection-final.txt) |
| 로컬·플랫폼 app의 공개 Run/SSE OpenAPI·RunError handler, 문법/import 검사 | 통과, 외부 연결 없음 | [bootstrap-smoke.txt](bootstrap-smoke.txt) |

342개 skipped는 외부 DB·별도 연계 설정 등이 필요한 테스트를 포함한다. 이를 전체 통과로 계산하지 않는다. PostgreSQL 84개는 관련 모듈만 격리 DB로 별도 실행한 결과이며, 마지막 3개는 그 중복 재검증이다. 서로 다른 실행의 개수를 고유 테스트 총수처럼 합산하지 않는다. 위 시간은 테스트 실행 시간이며 서비스 응답 지연/처리량 지표가 아니다.

## 확인한 경계

- Worker claim 없이 실행 준비 DB 작업이 시작되지 않는다.
- 준비 AsyncSession이 graph 호출 전에 닫히고, 결과 반영은 새 AsyncSession을 사용한다. 실제 PostgreSQL에서 session 두 개의 생성·종료를 관찰했다.
- 최초 입력 receipt 이후에는 mutable 프로젝트 prompt를 다시 읽거나 모델을 다시 호출하지 않고 결과 projection을 복구한다.
- 사용자 resume의 interrupt 주소·receipt, 중복 요청·오래된 승인·모델 pin, 동일 세션 잠금과 다른 세션의 독립 진행을 유지한다.
- Executor binding·sequence·receipt와 빠른 결과 재전달, decision/repair HITL 이후 결과 반영을 유지한다.
- 초기 stream의 과거 상태 echo는 일치하는 receipt 이후에만 서비스에 반영한다.
- 중첩 graph invocation은 취소 감시의 제출 부작용 tracker를 공유한다. 서로 다른 invocation은 격리된다. 가능한 외부 제출 이후 취소를 안전한 로컬 취소로 오판하지 않는다.
- Run application 모듈은 FastAPI/Starlette를 직접 import하지 않는다. 의미별 예외가 기존 HTTP Problem 계약으로 변환된다.

## 재현

저장소 루트에서 기존 Python 환경을 사용한다. 의존성은 기존 lock이며 신규 라이브러리는 추가하지 않았다.

```sh
PYTHONPATH=src .venv/bin/python -m pytest -q \
  src/api_service/test src/agent_service/agents/analysis/tests \
  --disable-warnings --maxfail=3
```

실제 DB 검사에는 **전용으로 비워도 되는 DB**를 사용해야 한다. identity fixture는 guard된 테스트 DB의 테이블을 비우고 schema를 준비한다. 본 검증은 기존 컨테이너와 별개인 `postgres:17` 컨테이너를 127.0.0.1:63364에 열고 `identity_test`, `agentic_runtime_test`, `agentic_checkpoint_test`를 생성했다. 실제 서비스 DSN을 넣지 않았다. 최종 검사 후 검증용 컨테이너를 제거했다.

환경 설정:

- `DTEST_IDENTITY_TEST_DATABASE_URL`: 전용 identity_test의 `postgresql+asyncpg://...` DSN.
- `DTEST_AGENTIC_TEST_SETTINGS_FILE`: 아래 두 키를 갖는 임시 JSON 파일. 실제 LLM 설정 파일을 사용하지 않는다.

```json
{
  "database_url": "postgresql+asyncpg://<test-user>:<test-password>@127.0.0.1:<test-port>/agentic_runtime_test",
  "checkpoint_db_uri": "postgresql://<test-user>:<test-password>@127.0.0.1:<test-port>/agentic_checkpoint_test"
}
```

```sh
PYTHONPATH=src .venv/bin/python -m pytest -q \
  src/api_service/test/test_run_boundaries_postgres.py \
  src/api_service/test/test_run_cleanup_postgres.py \
  src/api_service/test/test_public_run_postgres.py \
  src/api_service/test/test_graph_runtime_postgres.py \
  src/api_service/test/test_run_concurrency_postgres.py \
  src/api_service/test/test_initial_request_recovery_postgres.py \
  src/api_service/test/test_user_resume_recovery_postgres.py \
  src/api_service/test/test_model_selection_postgres.py \
  src/api_service/test/test_planning_api_postgres.py \
  src/api_service/test/test_agentic_execution_api_postgres.py \
  src/api_service/test/test_agentic_repair_api_postgres.py \
  --disable-warnings --maxfail=3
```

## 범위와 제한

LLM·Executor 응답은 fixture이며 LangGraph/checkpointer·PostgreSQL 처리와 API 호출은 실제 구현이다. 실제 LLM endpoint, 실제 Executor 컨테이너, 사내 SSO SDK, Kubernetes를 포함하는 E2E/용량 검증은 수행하지 않았다. 기존 원본 checkout/.env와 실행 컨테이너·실제 DB 자료를 변경하지 않았다.

현재 dispatcher 둘의 총 실행 한도는 통합하지 않았다. GraphInvocation 공통화와 DB session 수명을 확인한 결과로 처리량 향상을 주장하지 않는다. 공통 명령 scheduler·깨우기 신호 구현 뒤 동일 총한도에서 혼합 입력/Executor 결과 폭주를 비교한다.
