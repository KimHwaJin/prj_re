# 공통 Worker 실행 자리 공유: 소규모 비교

`test_capacity_probe.py`는 이전 분리 Worker와 현재 공통 Worker를 같은 파일로 검사한다. 실제 PostgreSQL 접수/claim/소유권/완료와 이전 Redis command dispatch를 실행하고, graph 작업만 `asyncio.sleep(5)`로 대체한다. 실제 LLM·Executor·Jupyter를 호출하지 않는다.

이는 준비된 대기열 8개를 처리하는 작은 burst 비교다. 사용자만8 / 결과만8 / 사용자4+결과4를 각각2회 수행한다. 이전은 사용자2+결과2, 현재는 공통4로 총 실행 한도를 맞춘다. 현재의 종류별4는 각 종류가 사용할 수 있는 최대치이며 합계는4다. 별도 한도에서 한쪽이 비면 다른 쪽이 자리를 사용할 수 있는 효과를 확인한다. 배포 설정32 등의 실제 처리량이나 지속 유입 한계를 측정하지 않는다.

## 계측 경계

- 준비: 전용 DB migration, 테스트 인증/사용자/세션/Run, Inbox ingest·routing, 이전 Outbox의 Redis publish, 풀 warm-up. 시간에서 제외한다.
- 시작: 대기열 전체 준비 후 두 실제 Worker loop를 시작한다. 현재는 공통 graph Worker+Ingress, 이전은 사용자 Worker+Ingress/Redis Dispatcher다.
- 종료: 모든 사용자 invocation `success`, 모든 event source command `DONE`, 현재 공통 명령 `DONE`를 실제 DB로 확인한다. 종료 drain 시간은 제외한다.
- graph queue: 공통 측정 시작부터 fixture graph 진입까지다. 요청 최초 접수부터의 대기 시간과 다르다.
- graph return: fixture의5초 작업 반환 시점이다. 전체 완료 시간은 마지막 DB 완료 상태 관찰까지다. 약50ms 간격의 완료 관찰 오차가 포함된다.
- 부가 SQL 수는 SQLAlchemy before_cursor_execute와 psycopg AsyncCursor.execute hook의 계측 수이며 완료 관찰/주기 metrics·routing도 포함한다. driver ping 등 모든 서버 SQL을 집계한 값은 아니다. 총 SQL 수를 업무당 DB 비용이나 유휴 조회 최적화의 효과로 해석하지 않는다.
- CPU는 시험 프로세스의 `process_time()` 차이다. PostgreSQL/Redis CPU, 실제 API HTTP/SSE/SSO, checkpoint/LLM/모델 호출 수, 외부 이벤트 접수 성능은 측정하지 않는다.

8개의 서로 다른 session, 중복 실행·누락·오류 없음, 총 graph 동시성4 이하와 종료 후 소유권/복구 잔여 없음도 검사한다. 모든5초 실행을 단일 graph 호출로 대신하므로 실제 다단계 분석 E2E와 구분한다.

## 안전한 재현

기존 서비스와 분리한 **삭제 가능한 localhost** PostgreSQL `identity_test` 및 Redis를 사용한다. fixture는 이 DB의 schema를 초기화한다. 운영/공유 DB 연결을 주지 않는다. Redis는 시험별 UUID namespace만 사용하고 정리한다. config/env의 서비스 endpoint는 쓰지 않는다.

비교 소스를 별도 임시 디렉토리에 `git archive`로 추출하고 동일 probe를 복사한다. 서로 다른 소스의 migration이 같은 DB를 초기화하므로 두 버전을 **동시에 실행하지 않는다**. Python/lock·DB/Redis·호스트·풀8·동시성·지연을 같게 유지한다. 프로젝트 루트에서:

```sh
DTEST_IDENTITY_TEST_DATABASE_URL='<disposable localhost identity_test asyncpg URL>' \
DTEST_COMMAND_TEST_REDIS_URL='<disposable localhost Redis URL>' \
DTEST_CAPACITY_OUTPUT='<fresh output JSONL path>' \
DTEST_CAPACITY_REPEATS=2 DTEST_CAPACITY_DELAY_SECONDS=5 \
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 \
<existing Python 3.11> -m pytest -q -s \
  scripts/benchmarks/command_worker/test_capacity_probe.py --disable-warnings
```

출력 JSONL은 append하므로 매 실행 새 경로를 쓴다. 기존 API·Agent 회귀 경로 밖에 있으며, 전용 DB 환경변수 없이 실행하면 fixture guard에서 skip된다. `DTEST_CAPACITY_DELAY_SECONDS=0`, repeats1은 probe smoke용이며 그 결과를 현실적인 LLM 응답시간 비교로 사용하지 않는다. 기본 한도 비교와 실제 측정 근거는 [2026-10-03 보고서](../../../docs/reports/command-worker-capacity-2026-10-03/README.md)를 따른다. 전체 단계5 성능 검증은 별도다.
