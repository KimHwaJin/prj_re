# Executor 이벤트 복구·중단 원인 진단 — 2026-10-04

**기본 Executor 주소에서 이벤트 이력 경로가 잘못 조립되던 문제는 수정·검증했다. 기존 50명 중단의 최초 원인은 여전히 미확정이다.** 50명 대형 분석을 진단 계측 포함으로 두 번 실행했으며 100개 흐름·2,100 POST가 모두 완료됐다. 재현되지 않았다는 사실을 기존 장애 해결로 해석하지 않는다. observations 저장 후보의 구버전 reader 역방향 호환 문제도 실제 PostgreSQL로 확인했다. 베이스 병합·푸시·배포는 보류 상태다.

## 기존 문제와 변경

중앙 설정이 Worker base를 Agent와 통일한 뒤에도 이력 요청은 `/executions/{id}/events`에 고정돼 있었다. 기본 `EXECUTOR_BASE_URL=http://executor:8080` 및 Agent의 `/api/v1/executions...` 경로에서는 이력 요청만 다른 주소가 됐다. 이제 `EXECUTOR_EXECUTION_PATH` + `/events`에서 Worker 경로를 유도하며, 필요할 때만 `EXECUTOR_EVENTS_PATH`로 지정한다. 구 별칭 `EW_EXECUTOR_EVENTS_PATH`도 같은 입력으로 정규화한다. base에 API prefix가 포함된 구성은 그에 맞는 기존 PATH 설정을 유지한다. 잘못된 placeholder/외부 URL/query 경로는 startup에서 거절한다. HTTPX는 base prefix를 유지하며, 이벤트 조회도 자동 proxy 환경 참조와 redirect를 사용하지 않는다.

Inbox의 연속 순번 routing, Redis ACK 이후의 DB 보존, 명령 원자 접수, 같은 세션 predecessor/소유권, POST 재제출 금지와 sync durability는 유지했다. 단순 sorting을 새로 넣거나 대기·오류 이벤트를 건너뛰지 않았다.

## 어떤 검증을 했는가

최종 관련 회귀 **105 passed, 실패·skip 0**. 독립 재검산98개도 통과했다([검산 결과](validation.json), [검산 코드](verify.py)). [로그](regression.log), [JUnit](regression.xml).

- PostgreSQL Inbox에서 역순·중복 발행, 동시 Router, 동일 sequence 충돌 거절.
- 누락 이벤트를 이력으로 보충해 1→2→3 순서로 원자 명령 생성.
- 이력 404/빈 페이지/다른 Execution/잘못된 항목에서 순번 0·Inbox를 보존하고 backoff. 늦은 이벤트 도착 후 정상 재개.
- reclaim catch-up의 여러 페이지를 끝까지 처리하며 뒤 이벤트를 누락하지 않음.
- **실제 HTTP/1.1 소켓 + 실제 PG**에서 root base 설정의 `/api/v1/executions/{id}/events?after_sequence=1` 호출과 1→2→3 처리 확인.
- 409/응답 유실/접수 후 checkpoint 오류를 진단 trace에서 구분. 코드는 제출하지 않고, 예외 타입·frame·HTTP status·command/event 순번·키 hash를 기록. 요청 body·code·원문 key는 기록하지 않음. 병렬 20명 identity 분리도 검증.
- 기존 네 가지 HITL/Executor wait·legacy cumulative pending write → 새 reducer 복원, 새 tagged pending write → 구 LastValue reader의 비호환 및 새 reader의 정상 재개 확인.
- 기존 공통 Worker 접수·세션 순서·소유권·취소·중복 및 Executor native async HTTP 전달 불확실성 회귀 유지.

## 전체 Worker 진단

측정 소스는 **2da80a9e97ab378f5271c5a15ffecd01f16342b7**로 고정했다. [소스·harness audit](source-audit.json), [원본 압축 SHA](evidence-index.json), [집계](summary.json)를 보존했다. 이후 추가된 실제 소켓 회귀는 측정 소스와 분리한다. 아래 r1/r2는 별도 controller 실행의 반복 라벨이다. 각 runner 내부 trial_index는 모두 1이며 디렉토리와 DB/namespace는 독립이다.

| 진단 | 흐름 | POST / 202 | 전송·명령 오류 | 남은 owner/recovery/Inbox |
|---|---:|---:|---:|---:|
| root 주소 + 강제 역순 이벤트 | 1 | 3 / 3 | 0 | 0 |
| 대형20 Operation, 50명 r1 | 50 | 1,050 / 1,050 | 0 | 0 |
| 대형20 Operation, 50명 r2 | 50 | 1,050 / 1,050 | 0 | 0 |

역순 실제 Redis 발행은 **1,6,5,4,3,2,9,8,7,10**이었으며 그래프 명령 순번은 **6,9,10**, 최종 관찰은 계획 순서의 4개였다. 이벤트 유형 필터 때문에 모든 원본 이벤트가 graph 명령이 되는 것은 아니다. 늦은 이벤트가 빠르게 들어와 이 E2E에서는 history GET이 0회였다. 이 결과는 실제 이력 HTTP 복구를 증명하는 시험과 구분한다.

대형 실행은 각 흐름마다 생성 1 + 추가 Operation 19 + finalize 1 = POST 21회였다. 기존 최초 오류 경계였던 Operation16 완료(event_sequence49)의 후속 POST는 100개 모두 expected_version33·202였다. 최종 관찰은 흐름마다 20개이며 명령 DONE, 중복 key/전송 오류/새 recovery가 없었다.

진단용 경과 시간은 r1 **94.87초**, r2 **93.40초**였고 사용자 평균은 93.40/91.88초였다. 새 trace가 추가됐고 기준 소스의 대조 실행은 없으므로 **성능 개선 수치로 사용하지 않는다.** 기존 067 시간 비교는 그대로 유지한다. LLM은 지연0 MockTransport를 실제 SDK/middleware에 연결했고 Executor는 loopback HTTP/Redis/manifest fixture다. 제출 Python 실행·실제 LLM·Jupyter·Pod/HPA·장기 작업 검증은 아니다.

## 기존 중단은 왜 아직 미확정인가

[기존 최초 오류 대조](historical-first-error.json)는 067 원본 SHA를 유지한다. 첫 오류는 Operation16의 완료 이벤트49 처리에서 ExecutorOutcomeUnknown이었다. receiver snapshot에는 해당 Execution의 Operation16까지 있고 이후 accepted 기록은 없다. **그러나 원래 HTTP 요청·상태 코드·예외 사슬이 없으므로 왜 다음 요청이 접수되지 않았는지, 혹은 어느 전송/후속 처리 단계에서 실패했는지는 확정할 수 없다.** 이벤트 역순, reducer, Worker 슬롯 고갈 중 하나로 단정하지 않는다.

이번 tracing은 benchmark에만 선택 적용했다. 실패가 나면 command/event49와 Executor status·expected_version·키 SHA·exception frame 및 DB 소유권을 함께 남길 수 있다. deadline 실패는 API 종료/클라이언트 취소 전에 모든 세션의 live DB/HTTP 증거를 보존한다. 409/응답 유실/접수 후 저장 실패는 테스트에서 구분했으나 과거 사건의 원인을 대신 증명하지 않는다.

## 실제 확인한 버전 제한과 남은 작업

새 checkpoint 저장이 실패해 tagged observations pending write가 남았을 때 구 LastValue graph의 `aget_state`는 observations를 list가 아닌 **dict**로 복원했다. 구 writer를 실제 invoke해 잘못된 상태를 소비시키지는 않았다. 새 graph는 같은 저장소에서 전체 4개 facts를 한 번만 복원하고 finalize를 한 번만 제출했다.

따라서 새 reader의 과거 호환과 구 reader의 역방향 호환은 다르다. 순차적으로 구·신 Pod가 같은 실행을 점유하는 혼합 배포도 자동 안전하지 않으며, 소유권 잠금만으로 이 버전 경계를 해결하지 못한다. 버전별 실행 고정 또는 writer 전체 drain/rollback 이행 등 계약은 후속이며 현재 자동 차단 기능은 없다. **066 저장 후보 병합 보류를 유지**한다. 이력 경로 수정만 필요하면 해당 runtime/config 변경과 관련 테스트를 독립 반영할 수 있다.

다음 성능 조사 후보는 API CPU sampling이다. 저장 후보 채택/배포 버전 경계와 기존 중단은 별도 미완료로 기록한다. 모델 호출 수 최적화·광범위 운영 기능·Dataset Registry/Workflow 후순위 정책은 유지한다.

## 재현과 정리

`run.py`에 `--executor-trace --diagnostic-timeout-seconds 180`을 지정하면 선택적 계측을 켠다. 역순 확인은 `--executor-root-base --reverse-event-batches`를 추가한다. 50명 원인 추적은 `--users 50 --observation-profile large20 --scenario executor --concurrency 20 --delay-ms 0 --checkpoint-profile`이었다. runner는 전용 loopback 63372/63373 외 DB/Redis 및 실제 Executor 제출을 거절하고 임의 이름의 새 DB/namespace만 소유한다.

[정리 기록](cleanup.json): 이번 임시 PostgreSQL·Redis 컨테이너와 anonymous volume을 제거했고, benchmark DB/namespace 및 identity_test도 정리했다. 기존 18개 컨테이너는 모두 유지했다. 원래 checkout/.env·실제 Executor 서비스는 수정하거나 재기동하지 않았다.
