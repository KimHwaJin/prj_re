# 실행 Worker 단일화 제안 (2026-10-03)

- **기준**: `feature/refactor-base` @ `e8d65a1`
- **관련 항목**: [구조 리뷰](2026-10-03-structure-review.md) R-04, R-09

## 검토 응답표

| ID | 항목 | 상태 | 의견 |
|---|---|---|---|
| W-01 | 방향: 입구는 두 개로 유지하고 graph 실행은 Run Worker 하나로 일원화 | 수용 | 입력 경로 분리·공통 graph 실행/한도 통합 수용. 큐 전달은 Redis Streams로도 가능하며 DB 원자성이 PostgreSQL polling을 요구하지 않음. |
| W-02 | 1단계: 공용 graph 호출·상태 반영 서비스 추출 | 수용 | 공통 GraphInvocation·상태 반영을 먼저 추출. checkpoint receipt·짧은 DB transaction·모델 pin·공개 Run ID 보존을 검증. |
| W-03 | 2단계: 이벤트 Worker가 graph 실행 대신 큐에 넣도록 전환 | 부분 수용 | 큐 전환 수용. API/EW DB가 다를 수 있어 단일 transaction 전제부터 확정. unique command·outbox·checkpoint 준비/순서 검증 후 handoff 제거. |
| W-04 | 3단계: 이벤트 Worker 배포를 경량화 | 부분 수용 | 이벤트 수신부에서 graph 실행 의존을 제거하는 방향 수용. 별도 Worker Deployment 대신 기존 단일 컨테이너 내부 역할 경량화. |
| W-05 | 결정 필요: 큐 테이블 선택(`agent_runs`에 kind 추가 / 별도 `session_commands`) | 부분 수용 | 공개 Run과 내부 실행 명령을 분리하는 안 권장. DB 명령 원장 + Redis 전달; 기존 ew_commands 재사용 범위와 schema migration은 후속 설계. |

## 현재 구조 (확인)

```
[HTTP POST /runs] → agent_runs(pending) → Run Worker (DB polling, SKIP LOCKED)
                                            └ RunService.create(_execute_existing=True)
                                               → graph.ainvoke (새 요청 / HITL 재개)
                                               → RunService._finalize_state 로 API 상태 반영

[Executor 이벤트] → Redis Stream → Ingress → inbox → Router → outbox → Dispatcher
                                            └ LangGraphEventAdapter
                                               → graph.astream (Executor 결과로 재개)
                                               → InvocationProjection
                                                 + synchronize_executor_completion 로 API 상태 반영
```

두 Worker가 **같은 session의 같은 LangGraph thread**를 각자 실행한다.

- Run Worker: [agent_run_worker.py](../../src/api_service/agent_run_worker.py)
- 이벤트 Worker: [worker_main.py](../../src/api_service/agent_worker/worker_main.py), [langgraph_adapter.py](../../src/api_service/agent_worker/langgraph_adapter.py)

## 판단

**입구를 둘로 나눈 것은 타당하다.**

- 사용자 요청은 멱등키, 권한, admission이 DB 트랜잭션과 묶여 있어야 하므로 Postgres 큐가 맞다.
- Executor 이벤트는 외부 입력이라 중복 제거, sequence 순서, DLQ, 재전송 처리가 필요하므로 Redis Stream과 inbox가 맞다.

**실행부가 둘인 것이 복잡도의 주된 원인이다.**

### 1. 같은 thread를 두 곳에서 실행하므로 프로세스 간 핸드오프가 필요해졌다

- `SessionExecution`에 소유자 종류가 두 가지(`api_run`, `executor_event`) 있다.
- 이벤트 쪽은 소유권을 최대 1초 기다리고, 그래도 얻지 못하면 `DeferEvent`를 던진다([session_execution.py](../../src/api_service/services/session_execution.py) `run_event_owned`).
- 코드 주석에 경쟁 조건이 직접 기록돼 있다.
  - `worker_main.py`: "A fast Executor can finish before the submitting Run commits its wait"
  - `executor_completion.py`: "Event delivery can beat the initial Run's post-interrupt DB commit"

### 2. API 상태 반영 경로가 둘이다

- Run 경로는 `RunService._finalize_state`를 쓴다.
- 이벤트 경로는 `synchronize_executor_completion`을 쓰는데, `RunService`의 비공개 메서드(`_lock_run_and_task`, `_finish_run`, `_finalize_state`)를 외부에서 호출한다.
- 이벤트 경로에는 이전 graph용 분기와 status 매핑이 별도로 구현돼 있다.

### 3. 이벤트 Worker가 사실상 두 번째 Agent 실행 엔진이다

- Executor 결과로 graph가 재개되면 `execution_review`, `execution_repair_propose`, `execution_report`가 이어지는데, 모두 LLM을 호출한다.
- 그래서 이벤트 Worker도 graph, 모델, 체크포인트 pool, 메모리 Store를 모두 올린다(deploy에서는 별도 Deployment).
- 실행 시 필요한 처리가 양쪽에 각각 있다.
  - 모델 pin 검증(`validate_checkpoint_selection`)
  - 프로젝트 컨텍스트 로딩
  - `submission_scope`
- 동시성 설정이 `AGENT_WORKER_CONCURRENCY`와 `EW_DISPATCH_CONCURRENCY`로 나뉘어 있어서, LLM 부하를 한 곳에서 제어할 수 없다.

### 4. 이벤트로 재개된 실행에는 Run 레코드가 없다 (추론)

- 이벤트 경로는 `agent_runs`의 attempt와 lease 밖에서 실행된다.
- 그래서 취소, 재시도, 진단을 Run 단위로 추적하기 어렵다. 실행이 끝난 뒤에 "가장 최근 Run"을 찾아 상태를 덮어쓴다.

## 제안: 입구는 둘, 실행은 하나

```
[HTTP]     → agent_runs(kind=user_turn)        ┐
[Executor] → Redis inbox (중복·순서·binding 검증) │
             → agent_runs(kind=executor_event)  ┘→ 단일 Run Worker → graph → 단일 상태 반영
```

### 이벤트 Worker에 남는 책임

- inbox에서 `event_id` 기준으로 중복을 제거한다.
- binding을 확인한다(session, task, execution).
- 오래된 sequence와 비활성 execution의 이벤트를 걸러낸다.
- DLQ와 실패 이벤트를 관리한다.
- **inbox 처리 완료 표시와 Run 추가를 같은 Postgres 트랜잭션에서 커밋한다.** 둘 다 Postgres에 있으므로 이벤트가 정확히 한 번만 큐에 들어가는 것이 자연스럽게 보장된다.

### 이벤트 Worker에서 빠지는 책임

- graph 로딩, LLM 호출, 체크포인트와 Store 연결
- `executor_event` 소유자 종류와 1초 핸드오프
- `synchronize_executor_completion`(상태 반영은 Run Worker 경로 하나로 합친다)

### 설계 시 결정할 사항

1. **큐 테이블 (W-05)**
   - `agent_runs`에 `kind`를 추가하는 방법: 공개 Run API에서 이벤트 Run을 노출할지 숨길지 정책을 정해야 한다.
   - 내부 `session_commands` 큐를 따로 두는 방법: 공개 Run 리소스는 바뀌지 않고, 두 입구가 모두 이 큐에 넣는다.
2. **session 단위 순서**
   - 현재 `claim_one`은 활성 소유자가 있는 session을 제외하고 `created_at` 순으로 가져오므로, session 안에서는 들어온 순서대로 처리된다.
   - 이렇게 되면 "Executor가 최초 Run의 커밋보다 먼저 끝나는" 경쟁은 큐 순서 문제로 바뀌어 별도 처리가 필요 없어진다.
3. **재시도 의미 변경**
   - 지금은 `DeferEvent`를 던지고 Redis PEL로 재시도한다.
   - 바뀐 뒤에는 Run의 `next_attempt_at`으로 다시 예약한다.
   - `LangGraphEventAdapter`의 Defer/Ignore/Reject 판정을 Run 상태(재예약, 무시 종료, 실패)로 옮겨야 한다.
4. **이벤트 병합**
   - `execution.operation_completed` 바로 뒤에 `execution.completed`가 오는 경우를 처리해야 한다.
   - 큐에 넣는 시점에 합치거나, 실행 시점에 무시하도록 한다.
5. **graph receipt 유지**
   - `ew_receipts` 멱등 검사는 큐가 있어도 마지막 방어선으로 남긴다.

### 얻는 것

- graph를 실행하는 곳이 Run Worker 하나뿐이 된다.
- 소유권, 취소, 재시도, 상태 반영, LLM 동시성이 한 경로에 모인다.
- `executor_event` 핸드오프와 관련된 recovery 분기를 제거할 수 있다.
- 이벤트 Worker가 graph와 LLM 없이 실행되는 경량 프로세스가 된다. Redis 쪽의 2단 파이프라인(inbox → outbox → dispatcher)도 단순해질 수 있다.

### 비용

- 큐 경유 지연이 생긴다(polling 간격). 기존 LISTEN/NOTIFY나 `wakeup`으로 줄일 수 있다.
- 진행 중인 thread를 위한 전환 절차가 필요하다.
- 공개 Run API 정책을 결정해야 한다(W-05).

## 단계

1. **W-02: 위상 변경 없이 중복 제거**
   - 공용 `GraphInvocation` 서비스를 추출한다. 포함할 것: 모델 검증, 프로젝트 컨텍스트, `submission_scope`, 상태 반영, status 매핑.
   - `executor_completion`이 `RunService` 비공개 메서드를 쓰지 않도록 한다.
   - 위험이 낮고 효과가 즉시 나타난다.
2. **W-03: 이벤트 핸들러 전환**
   - 이벤트 핸들러가 "graph 실행" 대신 "큐에 넣기"를 하도록 바꾼다.
   - `executor_event` 소유자 종류와 핸드오프 로직을 제거한다.
3. **W-04: 배포 경량화**
   - 이벤트 Worker 배포에서 graph, LLM, 체크포인트, Store 의존성을 제거한다.

## 개발 검토 응답 (2026-10-03)

원문은 보존했다. 동일 DB/트랜잭션 전제, 순서와 멱등 보장, 현재 내장 Worker의 자원 공유, 공개 Run과 내부 명령의 구분에 대한 보완은 [상세 응답](2026-10-03-review-response.md#worker-통합-제안)을 참고한다. Redis 전환 및 Worker 통합 구현은 아직 시작하지 않았다.
