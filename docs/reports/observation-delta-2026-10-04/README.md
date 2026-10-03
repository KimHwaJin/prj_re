# observations 증가분 쓰기 후보 비교 — 2026-10-04

큰 미리보기를 20개 Operation으로 받는 조건에서 표준 reducer 후보는 체크포인트 누계 **9.405→6.430MiB, 31.6% 감소**다. pending observations write는 **90.5% 감소**하지만 full snapshot blob은 유지한다. 실행 시간 단축은 입증하지 못했다. 이 후보를 `feature/observation-delta-checkpoints`에 구현·검증했으며 **베이스 미병합·미푸시·미배포**다. 처리량 우선 원칙에 따라 바로 운영에 채택하지 않는다.

공식 beta DeltaChannel(snapshot_frequency=5)은 같은 조건에서 3.916MiB로 58.4% 줄지만 진단 수행 시간이 948.3→1200.1ms(+26.5%)이고 추가 ancestor-history 조회가 생긴다. runtime 기본안으로 채택하지 않았다. 설정/배포 선택지를 늘리지 않고 비교 도구 안에서만 overlay로 실행했다. [공식 체크포인터 문서](https://docs.langchain.com/oss/python/langgraph/checkpointers)의 beta 저장 최적화와 설치된 라이브러리 소스를 확인했다.

## 무엇이 달라졌는가

기존 `process_event`는 매 Operation마다 `old observations + new facts` 전체를 노드 write로 보냈다. saver는 누적 목록을 channel blob에도 저장하고 노드의 pending write에도 저장했다. 후보는 노드 write에 이번 결과만 보낸다. LangGraph 표준 BinaryOperatorAggregate reducer가 합쳐서 모든 Agent reader·공개 response에는 종전과 같은 전체 list를 제공한다. full blob은 과거 복원/빠른 직접 읽기를 위해 그대로 남긴다. DB 테이블·연결 pool·LLM·실행 정책·durability sync·승인 원문·Executor body·receipt·raw output 보존 범위는 변경하지 않는다.

단순 `operator.add`로 바꾸면 구버전 pending write가 누적 전체 목록일 때 prefix가 중복된다. checkpoint 3개 + 구버전 누적 pending write 4개는 7개가 아니라 4개가 되어야 한다. 따라서 새 append는 `{append_operation_facts: facts}` 내부 태그로 구별하고, 기존 plain list는 전체 교체로 처리한다. 새 receive에서만 `Overwrite([])`로 초기화한다. FAILED/NOT_RUN/repair 성공은 순서대로 모두 남기며 tool ID로 제거하지 않는다. 중복 이벤트 차단은 기존 receipt가 담당한다. 내부 태그는 API/SSE 응답 계약이 아니다.

관련 구현: `execution/observation_state.py`, `execution/nodes.py`, `state.py`, `planning/graph.py`. reducer metadata는 NODE_INPUTS에서도 `include_extras=True`로 유지한다. [개발자 규약](../../agent-development/analysis-state-lifecycle.md)을 참고한다.

## 비교 조건과 원본

- baseline: 실제 5ca22a5의 state/lifecycle/nodes/graph 소스를 Git에서 읽어 격리 프로세스에 로드한다. checkout은 전환하지 않는다. 시작 당시 최초 v1 코드와의 비교가 아니다.
- append: 후보 working tree. delta: 같은 후보의 state 선언만 DeltaChannel5로 바꾼 격리 overlay. runtime에 beta 코드를 남기지 않는다.
- 13조건×5회×3방식=**195회**. 3회 후 시간 편차가 커 2회 더 수행했다. 반복별 variant 순서를 교차하고, 조건 순서는 같은 seed로 섞었다. 프로세스마다 제외된 단일 Tool warmup 1회. 소스·하네스·의존성 해시 동일성을 검산했다.
- 실제 PostgreSQL17 임시 컨테이너, loopback63378, pool min1/max2. 실제 production graph와 등록 Python 함수를 실행하는 Executor double. 역할 응답은 0초 fixture. 실 HTTP/Worker/Redis/LLM/Executor/Jupyter 및 동시 서비스 부하는 포함하지 않는다.
- 기존 065와 같은 raw output0~16MiB, 20 Tool을1/5/20 Operation으로 나누기, 20 Tool 큰 미리보기1/20 Operation, repair0/1/5/10. repair10 ceiling은 fixture에만 적용하며 서비스 기본3은 그대로다.
- 각 trial은 새 thread/파일 경로. completion/replay, full graph 5회 read, pool 종료·새 pool/graph 복원, wait 재진입, 실제 terminal event 재개까지 확인한다. 이어 새 FAQ 요청과 과거 checkpoint를 읽어 reset·이전 분석 문맥 보존·과거 승인/body/근거 불변을 확인한다.
- [raw.json.gz](raw.json.gz)는 약180MiB 원본 JSON의 압축본이다. source/harness SHA, 실제 serializer/SQL 행, trial 순서, 모든 결과를 포함한다. [runs.json](runs.json), variant별 로그, [summary.json](summary.json)에 재현·집계를 남겼다. 사내 인증정보/실 사용자 입력은 포함하지 않는다.

## 5회 평균

| 조건 | 기존 MiB | 표준 reducer MiB | Delta5 MiB | 기존 ms | 표준 reducer ms | Delta5 ms |
|---|---:|---:|---:|---:|---:|---:|
| large_operations_1 | 1.156 | 1.156 | 0.844 | 250.8 | 218.9 | 251.7 |
| large_operations_20 | 9.405 | 6.430 | 3.916 | 948.3 | 1000.6 | 1200.1 |
| operations_1 | 0.549 | 0.549 | 0.538 | 211.3 | 212.5 | 232.3 |
| operations_20 | 3.045 | 2.940 | 2.860 | 839.1 | 845.0 | 1113.2 |
| operations_5 | 1.088 | 1.067 | 1.050 | 382.9 | 332.0 | 455.8 |
| output_0 | 0.304 | 0.305 | 0.305 | 150.4 | 163.7 | 175.7 |
| output_1048576 | 0.335 | 0.335 | 0.320 | 179.4 | 164.7 | 205.9 |
| output_16777216 | 0.335 | 0.335 | 0.320 | 178.0 | 178.6 | 201.5 |
| output_65536 | 0.335 | 0.335 | 0.321 | 175.7 | 151.2 | 195.9 |
| repair_0 | 0.331 | 0.331 | 0.330 | 213.2 | 205.2 | 198.1 |
| repair_1 | 0.509 | 0.509 | 0.506 | 193.4 | 206.7 | 246.7 |
| repair_10 | 2.322 | 2.283 | 2.259 | 539.4 | 576.3 | 703.0 |
| repair_5 | 1.265 | 1.254 | 1.245 | 365.8 | 410.5 | 426.5 |

시간은 timed 초기 요청/승인/이벤트/중복 재개 호출의 합이며 Python double 실행·파일 I/O를 포함한다. SQL capture, saver read5회, full graph read5회, pool 재생성/graph build, wait 재진입, 후속 reset 검사는 제외한다. 표본은 condition당5개다. 신뢰구간/서비스 p95/용량을 산출한 측정이 아니다. fixture 경로 길이·version 문자열의 미세 차이도 포함하므로 수십/수백 bytes 차이를 개선 효과로 해석하지 않는다. 범위와 원시값은 summary/raw에 있다.

### 큰 미리보기 + 20 Operation

| 지표 | 기존 | 표준 reducer | Delta5 |
|---|---:|---:|---:|
| 전체 누계 MiB | 9.405 | 6.430 | 3.916 |
| 수행 평균 ms | 948.3 | 1000.6 | 1200.1 |
| 수행 최소~최대 ms | 862.3~1055.0 | 839.8~1186.2 | 1136.1~1250.7 |
| full graph warm read 평균 ms | 3.94 | 3.35 | 4.30 |
| 새 pool+graph+첫 full read 평균 ms | 34.60 | 28.24 | 37.79 |
| ancestor-history method 추가 호출/trial | 0 | 0 | 65 |

표준 reducer는 누적 blob 저장량이 동일하고 pending write가 약3.30→0.31MiB로 감소한다. 실행 평균은 오히려 **+5.5%**이며 범위가 겹친다. 개선/악화가 반복 가능한 원인이라고 확정할 근거는 부족하다. 작은 미리보기20 Operation에서는 저장량3.4% 감소, 시간839.1→845.0ms(+0.7%). repair10에서는 저장량1.7% 감소, 시간539.4→576.3ms(+6.9%). 모든 조건이 빨라졌다는 결론은 틀리다.

Delta는 blob snapshot 빈도까지 줄인다. 대신 설치된 PG saver가 ancestor write를 조회해 전체 list를 복원한다. 위65회는 method 호출 수이며 SQL statement 수가 아니다. 총 저장량 외 metadata/본문 보존 비용은 여전히 남는다. 특히 **latest encoded bytes가 작아도 전체 복원 상태/heap이 작다는 의미가 아니다**. 과거 ancestor/pending write가 필요할 수 있다. 단일 큰 배치가0.844MiB로 줄어도 전체 운영에 유리하다고 단정하지 않는다.

## 검증과 호환 범위

- Agent 전체 및 패키지 경계 **324 passed**, actual PG 관련 **13 passed**, skip0. XML/log 보관. 324개 회귀의 67 warnings는 checkpointer 없는 inner role의 durability 경고다. 실제 PG 13개에는 경고가 없다.
- 실제 baseline graph의 plan/Executor/decision/repair HITL waits를 PG에 저장하고 pool 종료 후 신규 graph로 재개했다.
- 구버전 누적 pending write는 저장되고 checkpoint commit만 실패한 상황을 주입했다. seed3/pending4가 신규 복원4개가 되고 finalize/terminal을 중복 없이 완료하는 것을 검증했다.
- 195회 모두 완료/승인·body 보존/중복 재제출 없음/실패와 repair 이력·순서 보존/새 요청 reset/과거 checkpoint read/pool 반환. 모든 방식·반복에서 observation 의미 필드 SHA가 동일하다.
- 독립 분석 **5231개 검산**: 실제 SQL bytes·blob/version·serializer 합·checkpoint/telemetry 대응·case와 source/harness·동일 결과·복원. 저장량/semantic hash/pool 복구/불필요한 ancestor-read를 각각 깨뜨린 4개 대조를 모두 거절했다.

구버전→신규 reader 방향을 검증했다. **신규 tagged pending write를 구버전 LastValue worker가 읽는 역방향 혼합 배포는 지원하지 않는다.** 베이스 병합·배포 전 새/구 worker가 진행 중 같은 graph 작업을 임의로 이어받지 않게 버전 경계를 확인해야 한다. 이 기록은 기존 더 오래된 설문 Runtime·Pod 강제 종료·실 커널 재시작·1주 대기·멀티 Pod rolling upgrade를 검증한 것이 아니다.

## 판단과 다음 작업

증가분 후보 구현과 로컬 저장·복원 A/B는 완료했다. 표준 reducer 후보를 이 파생 브랜치에 보존하고, Delta는 기본 코드로 채택하지 않는다. **저장 효율 후보와 처리량 개선 확정은 구분**한다. 실제 성능 우선 채택 결정은 다음에 기존 방식/표준 reducer의 Worker 동시 유입 A/B로 확인한다. 슬롯/pool/LLM fixture/폴링 조건을 같게 두고 DB 쓰기 시간·큐 대기·E2E·처리량을 비교해야 한다. 그 결과가 불리하면 후보를 베이스에 반영하지 않는다. 모델 호출 횟수 개선이나 metadata pruning/TTL을 섞지 않는다.

시험용 PG의 연결0/정확한 컨테이너 ID를 확인해 제거했고 생성 fixture 디렉토리도 정리했다. 기존 사용자 서비스는 재설정하지 않았다. [cleanup.json](cleanup.json), [최종 검증](final-verification.json).

## 재현

기존 서비스 DB를 지정하지 말고 새 로컬 PostgreSQL의 `agentic_checkpoint_test`를 준비한다. 인증은 fixture 전용이다. `DTEST_DELTA_PROFILE_DSN`에 그 DSN을 넣고 아래를 방식/반복별 별도 프로세스로 실행한다. `--fixture-dir`와 `--output`은 새 경로여야 한다. 실제 sequence는 runs.json과 raw에 있다.

```bash
PYTHONPATH=src .venv/bin/python scripts/diagnostics/profile_observation_delta.py \
  --variant append --reference 5ca22a5 --repeat 1 \
  --fixture-dir /tmp/fresh-append-1 --output /tmp/fresh-append-1.json
.venv/bin/python scripts/diagnostics/analyze_observation_delta.py \
  docs/reports/observation-delta-2026-10-04/raw.json.gz /tmp/summary.json \
  --negative-controls
```

baseline/append/delta 각1~5회 결과를 raw의 variants 구조로 합쳐 독립 분석한다. gzip 해제 후 trials는 매 방식65행이다. 체크포인트 테이블과 node writes를 변조하지 않는다. pytest 실제 PG fixture는 identity_test 별도 DB에서 실행했고 profile DB와 구분했다. raw.stdout 체크섬은 기존처럼 전체 파일을 읽으므로 원본 파일 크기 증가 비용이 완전히 사라진다는 뜻이 아니다.
