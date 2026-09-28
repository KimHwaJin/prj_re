# Run 지연 원인분석 — 1단계 결과

2026-09-28. 로컬 `dtest-agent-loadtest`, API 18080, Worker 4개, LLM Mock 0ms, polling 0.25초, 사용자 생각 시간 0초. 실제 Executor와 Mock Executor 모두 제출하지 않았다.

## 결론

**정체 Run의 직접적인 원인은 Graph 내부 연산이 아니라, Graph 종료 후 취소 감시 작업의 종료를 무기한 기다리는 처리다.** 실제 부하에서 정체를 재현했고, 70개 연속 snapshot이 `RunService._run_cancellable`의 `await cancel_task`를 가리켰다. 해당 Run의 Graph 호출은 35.55ms에 끝났지만 Worker 실행 슬롯은 취소 정리 전까지 355.34초 점유됐다.

Python 3.11.15의 `asyncio.wait_for`와 SQLAlchemy 2.0.52의 연결 풀 큐를 사용하면, 내부 대기 완료와 바깥 task 취소가 겹칠 때 취소가 전달되지 않는 실행 순서를 재현할 수 있다. 실제 `RunService._run_cancellable`을 그대로 사용한 작은 재현은 20/20 정체, 이벤트 루프에 한 번 양보하는 대조군은 0/20 정체였다. 실서버에서 취소가 삼켜진 바로 그 순간은 snapshot에 없으므로, 라이브러리의 정확한 내부 호출 지점은 이 재현 경로와 구분한다. **한 번의 cancel 호출을 종료 보장으로 간주하고 무기한 기다리는 애플리케이션 문제는 실측으로 확인됐다.**

일반적인 지연도 별도로 존재한다. 25명에서 정상 완료 Run 실행 시간의 약 62%는 Graph 재생성과 결과 영속화에 쓰였다. 100명에서는 이 비용들과 Graph 실행 지연, 이벤트 루프 지연이 모두 증가했다. 다만 100명 구간은 정체 Run이 슬롯 하나를 점유했으므로 정상 Worker 4개의 순수 용량 시험으로 읽으면 안 된다. polling 기여도는 아직 대조 시험 전이다.

## 무엇을 바꾸고 시험했는가

- 제품의 실행·재시도·취소 정책과 부하 조건은 유지하고 선택형 계측만 추가했다.
- 구간: 연결 풀 준비/획득/반환/종료, Graph 생성, Graph 호출, chain callback, checkpoint 읽기/쓰기, 결과 영속화, 최종 commit, SQL verb별 실행 시간.
- 5초 초과 Run은 5초 간격으로 진행 중 span, 관련 asyncio await 경로, 연결 풀 통계를 저장했다. 입력/출력·SQL 인자·지역 변수는 저장하지 않았다.
- 단위·계약 검증 9개 통과. 실제 API smoke 4여정/16 Run 성공.
- 25명과 100명을 각각 30초 안정화 후 300초 측정했다. 동일 설정으로 연속 실행했고 각 단계의 통계는 초기화했다.
- 첫 부하 재시험에서 정체 지점이 포착되어 동일 부하의 두 번째 반복보다 작은 재현/대조 20회씩으로 원인 검증을 우선했다. 두 번의 부하 반복을 수행한 것으로 해석하면 안 된다.

## 실측 결과

| 지표 | 25명, 5분 | 100명, 5분 |
|---|---:|---:|
| HTTP 요청 | 36,119 | 94,202 |
| HTTP 실패 | 0 | 0 |
| 측정 창에서 끝난 여정 | 2,007 | 846 (성공 845, 실패 1) |
| 성공 여정/초 | 6.69 | 2.82 |
| 여정 p95 | 4.5초 | 40초 |
| 정상 반환 Run 실행 평균 | 137.78ms | 244.29ms |
| 성공 여정의 Run 큐 대기 평균 | 638.38ms | 8,413.49ms |
| 성공 여정의 Run 큐 대기 p95 | 883.20ms | 10,755.97ms |
| Run별 최대 이벤트 루프 지연의 p95 | 14.37ms | 105.24ms |

여정 p95는 Locust 히스토그램 근사값이며 실패 이벤트도 해당 FLOW 분포에 포함된다. Run 실행 평균은 측정 창에 정상 반환한 trace 표본(각각 8,041/3,379개)이다. 정체 Run은 측정 창 이후 취소했으므로 이 실행 평균에 포함되지 않는다. 준비·안정화·종료 대기를 포함한 전체는 성공 3,273여정, 실패 1여정이다. DB Run 증가량 13,093 = 3,273 × 4 + 정체 Run 1개로 일치했다. 진단 trace에는 별도 smoke 16개가 더해져 시작/종료 각각 13,109개가 기록됐다.

## 정체 Run의 증거

- Run: `35a72be7-a918-4b55-bb64-ab29bb0db4db`
- Session: `d05bdd2b-cfb2-4e71-8f9f-d9ab905b242d`
- PID: 11
- UTC 04:27:59.065 접수, 04:28:07.522 실행 시작.
- Graph 호출 35.55ms, 결과 영속화 210.65ms. checkpoint/bridge 연결 풀 종료도 이미 완료됐다.
- UTC 04:28:12~04:34:01의 70개 snapshot 모두 Worker의 대기 위치가 `run_service.py:104`였다.
- 같은 프로세스의 취소 감시 coroutine은 `_wait_for_cancellation`에서 조회와 sleep을 반복했다.
- 클라이언트는 120초 제한으로 실패했지만 서버 Run은 계속 running이었다. heartbeat도 갱신됐다.
- 정상 cancel API로 정리한 뒤 canceled가 됐고, 잔여 pending/running은 0개다.

관측된 흐름:

```text
Graph 완료 + 결과 영속화 완료
  → finally에서 cancel_task.cancel() 호출
  → 취소 감시 작업이 종료되지 않고 반복을 계속함
  → await cancel_task가 반환하지 않음
  → Run의 최종 상태 기록까지 도달하지 못함
  → heartbeat 유지, Worker 슬롯 하나 점유
  → 다른 Run의 큐 대기 증가
```

이는 “Graph 자체가 멈췄다”는 앞선 추정을 정정하는 결과다. `user_request is required` 오류와 같은 현상이라는 증거도 없다. 이번 API 로그에서 해당 문자열·traceback·진단 파일 쓰기 오류는 모두 0건이었다.

## 작은 재현이 보여주는 경쟁 조건

`SQLAlchemy AsyncAdaptedQueue.get(block=True, timeout=...)`는 내부적으로 `asyncio.wait_for(queue.get(), timeout)`를 사용한다. 설치된 Python 3.11.15 구현은 바깥 취소를 받았을 때 내부 Future가 이미 완료돼 있으면 `CancelledError` 대신 내부 결과를 반환할 수 있다.

재현에서는 DB 연결 획득을 실제 SQLAlchemy 큐로 대체하고, 실제 애플리케이션의 `_run_cancellable` 종료 로직을 호출했다. Graph 완료와 큐 반환이 겹치는 순서를 만든 경우 취소 요청 횟수는 1인데 감시 작업이 계속 실행되고, Run은 반환하지 않았다. 작업을 남기지 않도록 각 시험 뒤 추가 취소로 정리했다. 네트워크나 DB 데이터 변경은 없다.

```bash
.venv/bin/python scripts/diagnostics/reproduce_cancel_watcher_race.py \
  var/loadtest/diagnostic-phase1-20260928/cancel-race-reproduction.json
```

이 재현은 종료 경쟁 조건을 증명한다. 실서버에서 연결 풀 압박과 polling이 그 경쟁 조건의 발생 확률을 얼마나 높이는지는 별도 검증 대상이다.

## 정상 Run의 비용도 분리됐다

서로 겹치지 않는 최상위 구간의 Run당 평균이다. checkpoint/SQL/chain 시간은 이 구간들에 중첩되어 별도로 더하지 않았다.

| 구간 | 25명 | 100명 |
|---|---:|---:|
| Graph 매회 생성 | 43.88ms | 51.02ms |
| Graph 호출 (checkpoint 작업 포함) | 23.51ms | 76.76ms |
| 결과 상태·메시지·이벤트 영속화 | 41.12ms | 69.06ms |
| bridge/checkpoint 연결 준비 합계 | 10.37ms | 15.60ms |
| Task 연결·풀 종료·최종 저장 등 나머지 | 18.89ms | 31.86ms |
| 전체 실행 | 137.78ms | 244.29ms |

Graph를 호출할 때마다 `AgentGraphRuntime._graph_context`에서 두 연결 풀을 열고 `build_analysis_workflow_graph`를 실행한다. 따라서 Graph 생성 비용은 LLM Mock 지연 0ms여도 발생한다. 결과 영속화는 여러 메시지·로그·Task 이벤트를 처리하며, 25명 표본에서 Run당 SQLAlchemy SELECT 약 36회, INSERT 약 10회, UPDATE 약 11회가 기록됐다. SQL 시간과 결과 영속화 전체 시간은 같은 지표가 아니며, transaction·Python 처리·스케줄링 비용도 포함된다.

## 다음 순서

1. **취소 감시 작업의 정상 종료를 먼저 수정한다.** Graph 완료 시 명시적인 종료 신호를 보내고 감시 루프가 확인하도록 하며, cleanup 대기 상한과 상태 확정 정책도 함께 검토한다. `cancel()` 한 번만으로 종료를 보장하지 않는다.
2. 현재 작은 재현을 회귀 테스트로 바꾼다. 정상 완료, 사용자 취소, DB 오류, 동시 완료, 취소 신호가 소실되는 순서를 검증하고 감시 작업/DB 연결 누수를 확인한다.
3. 같은 25→100명 부하를 재실행해 정체와 슬롯 손실이 사라지는지 비교한다. 이 비교 전에는 Worker 증설 효과를 판단하지 않는다.
4. 그다음 polling 0.25→1→2초 대조로 API·DB 경쟁의 기여도를 분리한다. Graph/풀 재사용과 결과 영속화 최적화는 각각 별도 변경으로 검증한다.

이번 단계에서는 계측과 재현 코드를 추가했으며, **종료 정책의 실제 수정은 아직 적용하지 않았다.** 로컬 API에는 선택형 계측이 켜져 있고 부하 생성은 중지됐다.

## 검증과 한계

- 모든 단계의 HTTP 실패는 0건이지만, 전체 사용자 여정 실패는 1건이다. HTTP 202/200만으로 완료 성공을 판단하면 이 결함을 놓친다.
- Mock Executor 고유 제출 수는 전후 모두 33으로 변화가 없다.
- 진단의 `run_end.outcome=returned`는 함수 반환을 의미하며, 비즈니스 성공 상태를 뜻하지 않는다. 실제 성공·실패는 journey와 DB 상태로 대조했다.
- 계측 비용이 추가됐고 데이터는 이전 시험부터 누적됐다. 과거 60초 시험과의 작은 차이를 코드 회귀로 단정하지 않는다.
- 100명 구간은 정체 Run에 의한 슬롯 손실이 포함됐다. 나머지 정상 Run의 지연 증가는 확인되지만 원인별 기여도는 아직 분리하지 않았다.
- Watchdog는 같은 이벤트 루프에서 동작하며, 완전한 동기 블로킹 중에는 snapshot을 남기지 못한다. 이번 정체는 이벤트 루프가 계속 동작하는 비동기 대기였다.

## 원본

- 전체 집계: `var/loadtest/diagnostic-phase1-20260928/analysis.json`
- 부하 원본 및 전후 DB: `var/loadtest/diagnostic-phase1-20260928/repeat-1/`
- Run 및 정체 snapshot: `var/loadtest/diagnostic-phase1-20260928/traces/`
- 정체 Run 최종 구간 시간: `stalled-run-final-trace.json`
- 취소 전후 Run·Task 상태: `stalled-run-cleanup.json`
- 경쟁 조건 재현/대조 40회: `cancel-race-reproduction.json`
- 설치 버전 및 실제 라이브러리 소스: `runtime-cancellation-source.json`
- 코드·이미지 해시와 설정: `environment.json`
- 종료 검증: `final-verification.json`

집계 재실행: `.venv/bin/python scripts/loadtest/analyze_diagnostics.py var/loadtest/diagnostic-phase1-20260928`
