# 004. 그래프·체크포인트 자원 수명과 실행 점유 토큰

| 항목 | 내용 |
|---|---|
| 상태 | 구현·격리 PostgreSQL 검증 완료 / 배포 미수행 |
| 시작일 / 완료일 | 2026-09-28 / 2026-09-28 |
| 브랜치 | feature/refactor-graph-lifecycle |
| 출발 commit | a68e656 — 001 완료를 feature/refactor-base에 반영 후 분기 |
| 구현 commit | 3e19ee3 — refactor: reuse graph resources and bind immutable execution claims |
| 배포·DB migration | 미배포. 신규 migration 없음. 기존 0019까지 필요 |

**확인한 문제**

[AgentGraphRuntime](../../src/app/services/agent_graph_service.py)의 `open_graph()`는 실제 호출마다 graph context를 새로 열고 종료했다. 모델 의존성·binding store용 풀·checkpoint 풀·compiled graph를 반복 생성했다. 반대로 `get_graph()`는 캐시를 보유했으며, 다른 호출의 `open_graph()`가 그 캐시의 stack을 닫을 수 있었다. 자원 생성·재사용·종료 책임이 두 경로로 나뉘어 동시 사용에 안전한 기준이 없었다.

[Executor 이벤트 진입점](../../src/app/agent_worker/worker_main.py)도 이벤트 하나마다 checkpoint 풀과 그래프를 생성했다. API 측 캐시만 바꾸면 이벤트 재개에서는 계속 같은 비용이 발생하는 구조였다.

[Run Worker](../../src/app/agent_run_worker.py)는 PostgreSQL transaction 안에서 `lock_token`을 생성했지만 실행 함수에 그 토큰을 전달하지 않았다. 실행 직전에 Task를 다시 읽은 토큰으로 heartbeat를 구성했으며, 마지막 상태 확정은 자신이 처음 점유한 실행 세대인지 검사하지 않았다. 오래된 실행 핸들이 늦게 호출되었을 때 신규 소유자의 토큰을 따라가는 것을 막아야 했다.

**변경한 동작**

- API/Run용 그래프는 첫 사용에 한 번 초기화하고 서비스 lifespan 종료까지 재사용한다. 같은 event loop에서 최초 호출이 겹쳐도 초기화는 한 번이다. 메모리 checkpointer 모드도 호출 사이에 같은 saver를 유지한다.
- 모든 사용은 `async with runtime.open_graph()`로 차용한다. 사용자가 남아 있으면 shutdown은 새 차용을 즉시 거절하고 기존 사용자의 반환을 기다린다. 제한 시간 초과·shutdown 취소·다른 loop 접근 시 공용 풀을 먼저 닫지 않는다. 종료가 확인된 뒤에만 stack을 닫는다.
- 사용자의 예외·취소는 차용 횟수만 반환한다. 다른 실행이 사용하는 그래프를 교체하거나 닫지 않는다. 초기화 중 요청 취소도 공유 자원 생성/정리 작업을 중간에 버리지 않는다. 자원 소유권을 추적하지 않는 `get_graph()`는 호출처가 없음을 확인하고 제거했다.
- bootstrap은 새 lifespan에서 runtime의 접수를 시작한다. 서버 import/OpenAPI 생성/API 전용 cold start가 그래프 DB 연결을 미리 열지는 않는다. 실제 초기화에 실패하면 부분 생성 자원을 닫고 다음 초기화 시도는 가능하다.
- Executor 이벤트 Worker는 Worker lifespan당 checkpoint 풀·그래프·adapter를 한 번 생성한 뒤 여러 이벤트에 사용한다. Worker 실행이 끝나고 소비자 종료를 기다린 다음 닫는다. Redis 소비 경로/Executor API 규격은 변경하지 않았다.
- 풀을 재사용한 이후의 Run에도 진단용 pool stats를 등록한다. I/O timing wrapper를 다시 중첩하지 않아 같은 조회 시간을 중복 계측하지 않는다.
- queue 점유 transaction 안에서 `ExecutionClaim(run_id, task_id, lock_token, attempt)`를 만든다. `ClaimedRun`을 Worker에 전달하고 context-local로 바인딩한다. 헤더·요청 body·공개 응답에 lock token을 노출하지 않는다.
- 실행 시작과 마지막 Run/Task 상태 확정에서 행 잠금 아래 claim·현재 상태·복구 플래그·lease 유효성을 확인한다. heartbeat는 점유 당시의 토큰을 계속 사용한다. 점유 대상이 사라졌다고 실행 함수가 새 queue row를 만드는 것도 금지한다.
- 오래된 실행의 복구 기록 task에도 같은 claim이 전달된다. token/attempt가 이미 바뀌었다면 신규 소유자를 `recovery_required`로 덮어쓰지 않는다. 이전 [001](001-run-cleanup-stall.md)의 자동 재실행 금지 정책은 유지한다.

**수명과 DB 연결의 의미**

풀 객체를 서비스 수명 동안 유지하는 것이 한 세션에 연결을 계속 배정한다는 뜻은 아니다. 체크포인트 조회·저장에 필요한 동안만 연결을 빌리고 반환한다. HITL 또는 Executor 결과를 기다리는 동안에는 checkpoint가 DB에 남고 그래프 호출은 반환한다. 풀 자체는 다른 세션이 재사용한다. 실제 1주 대기 시험은 수행하지 않았으며, WAITING_EXECUTOR의 공통 상태/세션 잠금 정비도 후속 작업이다.

현재 유지되는 별도 자원은 다음과 같다. 같은 프로세스 안에서도 API/Run 그래프와 Executor 이벤트 그래프의 풀은 아직 통합하지 않았다.

| 경로 | 생성 단위 | 닫는 시점 |
|---|---|---|
| API CRUD SQLAlchemy | 기존 프로세스 공유 engine | 서비스 종료 |
| API/Run binding bridge + checkpoint + graph | 해당 runtime 첫 사용 1회 | 차용자가 모두 반환한 서비스 종료 |
| Executor 이벤트 Store/Redis/HTTP | 기존 Worker lifespan | Worker 종료 |
| Executor 이벤트 checkpoint + graph | Worker lifespan 1회 | 소비자가 끝난 Worker 종료 |
| Workflow catalog/history의 동기 psycopg 접근 | 기존 동작 유지 | 기존 개별 접근 범위 |

API의 `DATABASE_POOL_SIZE + DATABASE_MAX_OVERFLOW`, checkpoint의 `CHECKPOINT_POOL_MAX_SIZE`, Worker Store/bridge의 `EW_POOL_SIZE`는 서로 다른 상한이다. 예를 들어 레포 기본값으로 API/Run 및 이벤트 Worker가 모두 활성화되고 자원이 생성되면 주요 풀 상한 합은 `20 + 8 + 4 + 8 + 4 = 44`다. 이것은 **실측 연결 수나 운영 권장값이 아니며**, Workflow 등의 비풀 연결을 포함한 전체 연결 상한도 아니다. Pod/프로세스가 늘면 각 풀이 늘어난다. 이번에는 값 자체를 바꾸지 않았고 운영 DB 수용량에 맞춘 총량/동시성 검증은 남아 있다. 반면 호출·이벤트 횟수만큼 풀이 반복 생성되는 경로는 제거했다.

종료 차용 대기에는 기존 `SHUTDOWN_TIMEOUT_SECONDS`(기본 25초)를 사용한다. 초기화/실제 close가 취소를 무시하는 경우 이를 강제로 죽이거나 자원 소유권을 버리지 않는다. 기한을 프로세스 강제 종료 보장으로 해석하지 않는다. 자원 종료 실패 후 임의 재사용도 허용하지 않는다.

**검증**

- 신규 오프라인 테스트 14개: 20개 동시 차용의 단일 초기화, 반복 재사용, 부분 초기화 실패, 초기화 중 반복 취소, 차용 중 종료·기한 초과, 종료 중 즉시 거절, 다른 loop 사용 거절, 타 풀의 선행 close 방지, 이벤트 10회에서 풀/그래프 1회 생성, 반복 Run 진단 정보.
- 신규 PostgreSQL 테스트 6개: 실제 LangGraph·AsyncPostgresSaver·psycopg pool로 20개 서로 다른 thread/session의 HITL 중단과 재개, 런타임 종료·재기동 후 남은 세션 재개, pool 크기 최대 2와 호출 종료 후 연결 반환, stale/만료 claim의 시작 거절, 소유권 변경 후 늦은 완료 거절, 초기 토큰으로 heartbeat 상실 감지, 완료 claim의 재실행 차단.
- 풀 검증은 실제 업무 Agent 대신 최소 승인 그래프를 사용했다. 소유권 검증은 실제 CRUD·queue·heartbeat와 제어 가능한 그래프 대역을 사용했다. 외부 LLM·Executor·Redis를 호출하지 않았다. 처리량/지연 개선 비율을 측정한 부하테스트가 아니다.
- 이전 설정·사용자·종료 보호를 포함한 집중 검증 120개 통과 후 공유 풀 진단 테스트를 추가 검증했다. 최종 전체 회귀는 **214 passed / 19 failed**다. 001 결과 XML과 실패 이름 대조 결과 신규 실패 0, 기존 실패 19개 동일이다. [검증 요약](../reports/graph-lifecycle-validation-2026-09-28.json)에 조건·기존 실패 목록을 남겼으며 검증 전용 PostgreSQL 컨테이너는 제거했다.
- 기존 테스트용 Alembic fixture는 폐기 가능한 localhost `identity_test` DB를 초기화하고 0017→head→0017→head를 확인했다. 실제 서비스 DB를 대상으로 실행하면 안 된다.

```sh
export DTEST_IDENTITY_TEST_DATABASE_URL='postgresql+asyncpg://<user>:<password>@127.0.0.1:<port>/identity_test'
PYTHONPATH=src python -m pytest \
  src/app/test/test_graph_runtime_lifecycle.py \
  src/app/test/test_graph_runtime_postgres.py -q
PYTHONPATH=src python -m pytest src/app/test -q --tb=short \
  --ignore=src/app/test/test_select_features.py \
  --ignore=src/app/test/test_split_dataset.py
```

**남은 경계와 다음 순서**

이 claim 검사는 **CRUD의 실행 시작·heartbeat·마지막 상태 확정 경계**를 보호한다. 그래프 내부의 모든 checkpoint 쓰기, 메시지 저장, Executor 제출 등의 외부 부작용을 원자적으로 차단하는 fencing은 아니다. 소유권 변경과 다음 heartbeat 사이에는 이전 그래프가 진행할 수 있다. 따라서 이번 변경을 근거로 lease 만료 작업을 자동 재실행하거나 `recovery_required`를 임의로 지우면 안 된다.

API와 Redis 이벤트의 공통 실행 접수·세션 소유권 통합, checkpoint 쓰기 권한 검사, 복구 절차가 다음 실행기 단계다. 이 기반을 검증한 다음 제한된 동시 슬롯을 늘린다. 이번 브랜치에서는 API Run Worker의 처리 슬롯 수를 늘리지 않았다.

compiled graph의 의존성/프롬프트도 lifespan 동안 유지되므로 설정·그래프 버전·초기 빌드 때 읽은 catalog prompt 변경은 프로세스 재시작이 필요하다. 향후 Agent/model 선택은 명시적인 registry/version별 자원 경계에서 처리해야 하며 요청별 설정을 공유 객체에 덮어쓰면 안 된다. 실제 업무 Agent 전체의 병렬 부하, LLM HTTP client 정리, 동기 Workflow DB 접근의 풀링은 이번 검증 완료 범위가 아니다.

실제 Gaia 템플릿·Kubernetes probe/강제 종료·다중 Pod·공유 PV와 운영 DB 연결 예산 검증은 미수행이다. 기존 실행 환경을 재기동하거나 원격 push하지 않았다.
