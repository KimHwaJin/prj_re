# Executor HTTP 런타임

## 변경한 경계

Agent의 제출·추가 Operation·finalize·실패 후 cancel·보고서 artifact 업로드 및
실행/결과/notebook 조회를 `httpx.AsyncClient`로 통일했다. API 런타임과 이벤트
런타임이 각각 클라이언트 한 개를 열고 그래프에 주입한다. Run마다 클라이언트를
열지 않으며 요청 간 연결을 재사용한다. 같은 프로세스에 두 런타임이 있으면
그래프용 HTTP 풀이 두 개다. 이벤트 ingress에는 기존부터 별도의 비동기 reconciliation
조회 클라이언트도 있으며, 해당 클라이언트와 EW 설정은 이번에 변경하지 않았다.
따라서 이 설정은 서비스 전체 HTTP 연결 수나 replica 전체의 전역 상한이 아니다.
DB·LLM 연결 풀도 별개다.

API 그래프의 모든 borrow가 반환된 뒤 클라이언트를 닫는다. 이벤트 Worker에서도
처리 종료 이후 닫고 그래프 초기화 실패 시 열린 HTTP 자원을 정리한다.
기존 수동 실행 도구의 PostgreSQL/메모리 경로에도 소유권을 연결했으며 새 CLI는 추가하지 않았다.

외부 Executor의 작업 완료를 HTTP 연결로 기다리지 않는다. 기존대로 접수 응답을
받으면 checkpoint → EXECUTOR_EVENT interrupt로 현재 실행을 반환한다.
실제 작업이 1주일 걸려도 그동안 HTTP 요청·실행 슬롯을 유지하는 구조가 아니다.

## 설정

중앙 config > env > 기본값 우선순위다. 새 설정은 필요할 때만 재정의한다.

| 설정 | 기본값 | 의미 |
|---|---:|---|
| EXECUTOR_HTTP_MAX_CONNECTIONS | 8 | 런타임 하나의 최대 HTTP 연결 수. keep-alive 상한도 동일 |
| EXECUTOR_HTTP_CONNECT_TIMEOUT_SECONDS | 5초 | TCP/TLS 연결 대기 한도 |
| EXECUTOR_HTTP_POOL_TIMEOUT_SECONDS | 5초 | 기존 풀에서 사용 가능한 연결을 기다리는 한도 |
| EXECUTOR_TIMEOUT_SECONDS | 기존 30초 | 호출 전체 기한. 풀 대기·연결·송신·응답 스트림을 포함하며 개별 read/write 한도에도 사용 |
| EXECUTOR_HTTP_MAX_RESPONSE_BYTES | 16 MiB | 압축 해제 후 읽어들이는 응답의 최대 크기 |

```yaml
service:
  executor:
    EXECUTOR_HTTP_MAX_CONNECTIONS: 8
    EXECUTOR_HTTP_CONNECT_TIMEOUT_SECONDS: 5
    EXECUTOR_HTTP_POOL_TIMEOUT_SECONDS: 5
    EXECUTOR_TIMEOUT_SECONDS: 30
    EXECUTOR_HTTP_MAX_RESPONSE_BYTES: 16777216
```

유효한 양수만 허용한다. 응답이 큰 notebook/result 사용 환경에서는 응답 크기를
확인하고 한도를 정한다. 외부 작업 실행 기한인 EXECUTOR_OPERATION_TIMEOUT_SECONDS와
HTTP 응답 기한을 혼동하지 않는다.

기존 EXECUTOR_TLS_VERIFY를 유지한다. 검증 활성화 시 기존과 같은
`ssl.create_default_context()`의 기본 CA 정책을 클라이언트 생성 시 한 번 적용한다.
HTTP redirect와 환경변수의 프록시 자동 참조는 사용하지 않는다. 프록시가 필요한
배포는 별도 명시적 연결 설정 지원이 필요하다.

## 실패·취소와 재시도

HTTP 계층은 POST 자동 재시도를 하지 않고 요청 body의 기존 idempotency_key를
그대로 전송한다. 시작/추가 Operation/finalize/cancel/report의 키 생성 규칙도 유지한다.

| 관측 결과 | 처리 |
|---|---|
| 연결 풀 대기 초과, 연결 실패·연결 timeout | HTTP 요청 전달 전 실패. 기존 제한된 Worker 재시도 정책 적용 가능 |
| 명확한 4xx 거절(408/409/429 제외) | 자동 재시도하지 않고 오류 종료 |
| 429 | 접수 거절로 간주하고 기존 제한된 Worker 재시도 정책 적용 |
| POST 전송 후 timeout·응답 유실·취소, 408/409/5xx/redirect | 접수 여부 불확실. 복구 필요 처리, 자동 재제출 금지 |
| 시작/추가 제출의 잘못되거나 누락된 접수 응답, 잘못된 JSON, 응답 크기 초과 | 접수됐을 수 있으므로 복구 필요 처리 |
| POST 응답을 받은 뒤 해당 실행 구간에서 오류·취소 | 체크포인트/내부 반영 완료가 불확실하므로 복구 필요 처리 |
| GET 취소 | 일반적인 취소 전파. 같은 구간에 이미 POST가 있었다면 위의 복구 보호 적용 |

실행 구간별 SubmissionEffects를 ContextVar로 자식 그래프 task에 전달한다.
클라이언트에 현재 사용자/Run 상태를 저장하지 않는다. 여러 요청이 병렬일 때도
전달 전 실패한 한 요청이 다른 요청의 접수 가능성 기록을 지울 수 없다.

취소 watcher와 그래프 결과가 경쟁할 때도 POST 이후의 불확실성을 우선한다.
API Run은 기존 `recovery_required`와 실행 소유권을 유지하고, 같은 세션 신규 실행을
거절한다. 이벤트 경로도 공통 세션 실행 소유권을 복구 필요 상태로 남긴다.
기존 fail-closed 정책에 따라 해당 프로세스의 신규 claim도 중단된다.
이는 원격 작업이 취소됐다는 뜻이 아니다. 프로세스 재시작만으로 DB의 복구 잠금이 풀리지 않는다.

접수 불확실성의 자동 조회/복구, durable 제출 outbox, 관리자 복구 API는 이번 변경에
포함하지 않았다. 정확한 최초 key/body 및 원격 접수 상태를 확인하지 않고 Task 잠금을
해제하거나 graph를 처음부터 다시 실행해서는 안 된다. 특히 PATH 파일 staging을
재실행하면 날짜 경로 등이 바뀔 수 있으므로 동일 키로 다른 body를 생성하는 재실행을
안전한 재시도로 간주하지 않는다.

## Agent 개발자 사용법

서비스의 기본 그래프 구성에는 클라이언트가 자동 주입된다. 직접 그래프를 실행한다면
소유자가 수명을 관리한다.

```python
async with ExecutorClient(settings) as executor_client:
    graph = build_analysis_workflow_graph(
        deps, settings, checkpointer=saver,
        bindings=bindings, executor_client=executor_client,
    )
    await graph.ainvoke(input_state, config)
```

이 저수준 예시는 HTTP 자원 수명만 보여준다. 서비스 수준의 session 소유권,
submission_scope, API 상태 반영이 필요하면 기존 Run/이벤트 진입점으로 실행한다.
클라이언트를 state/checkpoint에 저장하지 않는다. 노드의 외부 HTTP는 await하고,
처리할 coroutine을 fire-and-forget으로 남기지 않는다.

기존 동기 테스트 어댑터는 call_io가 소유권을 유지하는 스레드 경계로 연결한다.
실제 HTTP 구현은 native async이며 run_sync로 감싸지 않는다. 파일/PV staging,
manifest 읽기, 기존 WorkflowStore 저장은 run_sync를 유지한다. 이 작업이 파일의
원자적 쓰기나 DB 저장소 전체 비동기 전환까지 완료했다는 의미는 아니다.
