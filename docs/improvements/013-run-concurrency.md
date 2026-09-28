# 013 — 프로세스별 Run 동시 실행과 대기 세션 보호

- 일자: 2026-09-29
- 기준: 012 `fb89dbc`를 `feature/refactor-base`에 fast-forward 병합한 뒤 분기
- 구현 브랜치: `feature/refactor-run-concurrency`
- 상태: 구현·격리 PostgreSQL·패키지 검증 완료. 베이스 병합·원격 push·배포 미수행
- 범위: API Run 실행기의 동시성, 동일 세션 접수 보호, Executor 완료의 API 상태 반영. 다중 Agent registry는 사용자 결정에 따라 후순위로 유지한다.

## 문제와 실제 변경

기존 실행기는 `claim_one()` 이후 해당 Run이 반환될 때까지 다음 Run을 점유하지 않았다. LLM I/O가 비동기여도 실행기 하나가 직렬로 호출하면 다른 세션의 Run은 기다린다. 이번 변경은 프로세스당 실행 중인 Run 수를 제한하면서 여러 세션을 함께 진행하도록 한다.

`AGENT_WORKER_CONCURRENCY`를 추가했다. 기본 1, 정수 1 이상이며 기존 config > env > 기본값 순서를 따른다. `agent_run_worker.run_forever()`가 실행 작업 집합을 관리하고 자리가 있을 때만 하나씩 점유한다. 빈 queue를 슬롯 수만큼 조회하지 않고, 가득 차면 실행 중인 작업이 끝날 때까지 기다린다. PostgreSQL의 기존 `FOR UPDATE SKIP LOCKED` 점유는 유지한다. API 프로세스/Pod가 늘어나도 같은 Run을 동시에 점유하지 않도록 하는 DB 책임과, 프로세스 안에서 몇 개를 실행할지 결정하는 책임은 별개다.

점유 commit 직후 취소되면 소유자를 잃을 수 있으므로 점유·실행 작업 등록 구간을 shield한다. 종료 요청 중 확정된 점유는 실행하지 않고 복구 필요로 기록한다. 실행 중인 자식 작업에는 취소를 전달하고 반환을 확인한다. 반복된 상위 취소도 정리를 버리지 못하게 한다. 종료나 점유 상태가 불확실한 작업은 기존 프로세스 건전성 정책으로 새 점유를 중단한다. 일반적인 업무 오류는 기존 RunService의 상태 기록·재시도 정책을 유지한다.

### 같은 세션의 대기 상태 보호

기존 DB active unique index는 pending/running만 포함한다. 실제 상태 모델은 HITL과 Executor 대기 모두 Task의 `waiting_input`을 사용하므로, 슬롯 반환만 보고 새 입력을 받아서는 안 된다.

API 접수 시 세션별 PostgreSQL transaction advisory lock으로 접수 경쟁을 직렬화하고, 미종료 Task가 있으면 새 사용자 입력을 409로 거절한다. 같은 idempotency key 재요청은 기존 Run을 반환한다. HITL에서 요청된 resume는 허용하지만 이미 다시 pending/running이 된 작업의 중복 resume는 거절한다. `interrupt.kind == EXECUTOR_EVENT`인 대기에서는 사용자 resume도 거절한다. 과거의 Task 없는 FAQ Run을 지정해 현재 대기를 우회하는 경우도 거절한다. 다른 세션은 별개로 접수할 수 있다.

이 접수 lock은 짧은 API transaction 동안만 보유한다. 외부 Executor 대기 동안 DB lock을 계속 잡는 구조가 아니다. DB enum이나 index를 바꾸는 마이그레이션은 추가하지 않았다. `WAITING_EXECUTOR`라는 새로운 저장 상태를 만들었다고 해석하면 안 된다.

### Executor 완료 후 API 세션 해제

대기 접수를 막으면서 기존 경로도 확인했다. 이벤트 Worker가 그래프를 끝내더라도 API Task/Run에 완료 상태를 연결하는 경로가 필요했다. `executor_completion.synchronize_executor_completion()`을 이벤트 adapter 뒤에 연결했다.

- 그래프에 다음 실행 단계가 남아 있으면 대기 세션을 해제하지 않는다.
- 그래프 task/execution 식별자와 해당 command/event의 영속 receipt를 확인한다.
- `Task.graph_task_id`로 API Task를 찾고 기존 Run → Task 순서로 잠근다.
- 초기 API Run의 대기 기록보다 이벤트가 빨랐다면 defer하여 후속 재처리에서 반영한다.
- 종료된 그래프의 execution status에 따라 API Run/Task를 success/error/canceled로 전이하고 Task 이벤트를 같은 DB transaction에 기록한다.
- 이미 끝난 Task의 중복 반영은 추가 이벤트를 만들지 않는다. 그래프 commit 이후 API DB 기록이 실패하면 receipt 재처리 경로에서도 이 반영을 다시 수행한다.

그래프와 API DB의 commit을 하나의 분산 transaction으로 만든 것은 아니다. 기존 receipt와 재처리를 이용한다. API Task가 없는 독립 그래프는 API 상태 변경을 생략한다.

## 검증

별도 `postgres:17-alpine` 임시 컨테이너의 `identity_test` DB에만 마이그레이션과 데이터 초기화를 수행했다. 기존 로컬 서비스/외부 DB/Redis/Executor는 변경하거나 호출하지 않았다.

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
DTEST_IDENTITY_TEST_DATABASE_URL=<격리된 identity_test DB URL> \
DTEST_CONCURRENCY_REPORT=<측정 JSON 경로> \
python -m pytest src/app/test src/agent_service/agents/analysis/tests -q
```

결과: **308 passed, 2 subtests passed, 0 skipped**, 55.13초. 경고 43개는 checkpointer 없는 내부 Agent의 durability 관련 기존 LangGraph 경고이며 새 경고 종류가 아니다. 기존 DB 의존 테스트까지 이번에는 실행했다.

주요 새 검증은 다음과 같다.

- 실행 한도 준수, 자리 반환 시 다음 Run 시작, 빈 queue의 단일 조회 루프
- 점유 commit과 shutdown 경쟁, 자식 작업 정리, 반복 취소, 실행 불확실성 발생 시 추가 점유 중지
- 같은 세션 동시 접수 10건 중 1건 수락/9건 409, 동일 키 재접수 5건은 같은 Run 반환
- 독립 DB session의 점유 요청 12개로 8개 Run을 중복 없이 점유·완료
- HITL 및 Executor 대기에서 lease 반환과 새 입력 거절, HITL 단일 resume 수락, 다른 세션 허용
- Executor 성공/실패 완료 반영, 초기 대기 기록과의 경쟁 defer, 중복 완료 반영 방지
- 오래된 Task 없는 FAQ resume를 통한 대기 세션 우회 거절
- config 기본값/환경변수/YAML 우선순위와 잘못된 동시성 값 검증

배포 wheel은 checkout 밖의 임시 디렉터리에서 새로 만들고 `python -I scripts/diagnostics/validate_agent_package.py <wheel>`로 검사했다. 체크아웃 import 없이 API OpenAPI, 7개 역할 Agent 구성·프롬프트, 기존 workflow 자산과 mock graph 흐름을 검사한다. 실제 모델/Executor는 호출하지 않는다.

### 실행 자리 1/2/4 비교

[조건·원시 수치 JSON](../reports/run-concurrency-validation-2026-09-29.json)에 재현 조건을 기록했다. 각 경우 다른 세션의 20개 Run을 먼저 접수하고, 그래프를 100ms async wait 후 성공하는 stub으로 대체했다. API 접수·DB·RunService·점유/감시/정리는 실제 실행했다. 테스트 DB는 NullPool, 단일 Python 프로세스이며 실행기 조회 간격 50ms, 취소 감시 간격 50ms, 테스트 완료 조회 간격 10ms다. 유저 think time을 모사하지 않는다.

| 프로세스당 실행 자리 | 20개 완료 시간 | 처리량 | 그래프 시작까지 평균 대기 | 관측 최대 동시 그래프 |
|---|---:|---:|---:|---:|
| 1 | 4.589초 | 4.358 Run/s | 2.282초 | 1 |
| 2 | 2.200초 | 9.092 Run/s | 1.025초 | 2 |
| 4 | 1.137초 | 17.594 Run/s | 0.533초 | 4 |

시간은 API 접수 완료 후 실행기를 시작한 시점부터 측정한다. 완료 시점은 DB success만이 아니라 실행 코루틴 정리까지 끝난 시점이다. 한 번씩 수행한 통제 비교이므로 실제 100명 사용자 성능·실제 LLM 처리량·운영 권장 동시성 4를 증명하는 수치가 아니다. 슬롯 수 증가에 맞춰 실제 동시 실행이 늘어나고 queue 대기가 줄어드는지를 확인한 결과다.

## 파일과 남은 제한

실행기·설정: `src/app/agent_run_worker.py`, `src/config.py`, `src/service_settings.py`, `.env.example`.
접수/이벤트 연계: `src/app/services/run_service.py`, `src/app/services/executor_completion.py`, `src/app/agent_worker/worker_main.py`.
검증: `src/app/test/test_run_concurrency.py`, `src/app/test/test_run_concurrency_postgres.py`, `src/app/test/test_graph_runtime_lifecycle.py`.
사용 설정은 [기동·설정 문서](../configuration-bootstrap.md)를 따른다.

- 이 제한은 API Run 실행기 프로세스 단위다. 별도 Executor 이벤트 Worker는 포함하지 않으며, 프로세스/Pod가 늘면 총 동시 실행과 DB 연결은 늘어난다. 전역 DB/LLM 예산, 접수 제한·공정성은 이번에 구현하지 않았다.
- 실제 여러 Pod, 실제 Redis → Executor → 그래프 → API DB 전체 연계, 실제 LLM, 공유 PV, 1주 대기/배포 교체는 이번에 시험하지 않았다. 이벤트 완료 반영 테스트는 통제된 그래프 snapshot과 실제 API DB를 사용했다.
- checkpoint 자체의 stale writer fencing, 종료 불확실 작업의 자동 복구, 외부 Executor 취소 확인은 여전히 후속 사항이다. 이번 슬롯 추가가 이를 해결했다고 보지 않는다.
- 012의 동기 I/O thread 종료 확인 정책을 유지했다. 모든 I/O를 native async로 전환한 변경은 아니다.
- 슬롯 기본값은 1로 유지한다. 기존 실행 중인 Docker 환경을 재시작하거나 설정을 4로 바꾸지 않았다.
- 다중 Agent registry, 모델 선택 확장, project_memory의 실제 저장은 이번 범위 밖이다.

이 파일과 구현은 같은 작업 commit으로 기록한다. 013의 베이스 병합과 배포는 별도 상태로 관리한다.
