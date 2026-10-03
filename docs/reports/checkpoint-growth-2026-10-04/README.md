# 대형 출력·Operation·repair 체크포인트 비용 — 2026-10-04 KST

**출력 파일의 크기 자체보다, Operation마다 늘어난 관찰값 전체를 반복 저장하는 비용이 다음 개선 대상이다.** 같은 20개 Tool과 같은 최종 계산 결과에서 Operation 1→20은 과거 포함 저장량 0.549→3.042MiB였다. 툴마다 64KiB 로그를 내고 16,000자 미리보기를 보존하면 1.157→9.413MiB였으며, 이때 observations blob+write가 약 70%를 차지했다.

이번 작업은 변경 전후 A/B나 서비스 용량 시험이 아니다. 현재 064 코드의 저장 비용을 분해한 로컬 진단이다. API·Worker·SSE·Redis·실제 LLM·실제 Executor/Jupyter를 거치지 않았고, 이 수치를 운영 응답 시간이나 동시 사용자 처리량으로 환산하지 않는다. production 코드·설정·배포는 변경하지 않았다.

## 조건과 범위

- 런타임 기준 `27fbefd`(064), 분기 `feature/checkpoint-growth-profile`. 실행 당시 working tree의 runtime/harness SHA와 설치 버전은 [원본](raw.json.gz)의 최상단에 기록했다.
- 실제 disposable PostgreSQL 17, loopback 63376, 별도 `agentic_checkpoint_test`. 기존 사용자 DB/컨테이너와 분리했다. lifespan pool min1/max2, `durability='sync'`, 현재 PooledAsyncPostgresSaver 및 공식 serializer/SQL이다.
- 현재 production planning/execution/receipt 경로와 실제 승인·등록 Tool Python을 사용한다. 모델 역할은 0초 deterministic fixture, Executor는 Python 함수를 실제 실행하고 manifest/stdout 파일을 작성하는 명시적 in-process double이다. 등록 함수는 진단 전용 catalog에만 추가한다.
- 13조건×3회=39회. fresh session/thread와 root, seed6504로 순서를 섞었다. 예비 1회 및 계측 준비 3회 자료는 최종 평균에서 제외했다. 최종 harness의 별도 warmup cohort는 없고 PG는 예비 시험 후 재시작하지 않았다. 첫 사용·host 활동 영향이 섞일 수 있다.
- Operation 비교는 **같은 20개 Tool**에서 review 정책만 바꿨다. 1회는 decision_boundary, 5회는 every_n_tools/4, 20회는 every_n_tools/1이다. 후자의 review 역할 호출과 사용자 진행 메시지도 추가되므로 단순 DB batch 수만 다른 조건은 아니다. 모두 승인된 정책대로 같은 최종 count=20을 얻었다.
- repair 비교는 같은 load→transform→finish 세 Tool에서 0/1/5/10회다. 초기 divisor=0 실패 뒤, 마지막 보정만 divisor=2로 성공한다. 중간 보정은 서로 다른 허용 범위의 음수다. editable literal의 level1이며 source는 수정하지 않는다. **측정용 서비스 ceiling만 10**이고 배포 기본 3회는 바꾸지 않았다. 성공 load를 다시 실행하지 않고 실패 transform과 미실행 finish만 이어간다.
- MULTI 완료는 finalize 접수 후 execution.completed를 받아 terminal까지 간다. Artifact POST/보고서 생성·model middleware 추론 비용·데이터 레이크/수백 GiB 데이터는 포함하지 않는다. 실제 장기 Executor 계산 시간을 기다린 시험도 아니다.

## 크기와 시간 정의

**과거 포함 논리 누계** = checkpoints의 JSON text bytes+metadata text bytes + unique checkpoint_blobs bytes + retained checkpoint_writes bytes. row key/index/WAL/TOAST/물리 페이지/Python heap/Executor 파일 저장량은 제외한다. `pg_column_size`도 원본에 있지만 이 표의 크기는 물리 디스크 사용량이 아니다.

**최신** = 마지막 checkpoint JSON+metadata+그 version이 참조하는 blobs. pending writes는 제외하며 live Python state와 동일한 크기도 아니다. 예를 들어 마지막 제출 body가 20개 Tool 모두를 담는지, 마지막 한 개만 담는지에 따라 최신 크기가 달라진다. 최신 값이 작다고 복구 데이터를 덜 보존했다는 뜻은 아니다.

**로컬 수행 시간** = setup/계획/승인 + Operation 이벤트 GraphInvocation·상태 읽기 + 중복 이벤트 재전달 + terminal 이벤트의 await 시간을 더한 값이다. 테스트 Executor의 Python 실행·파일 쓰기도 포함한다. SQL 캡처, 추가 검증 읽기, finalize wait의 5회 warm saver 조회는 제외했다. 각 조건 3회로 평균과 범위를 함께 제시하며, p95나 처리량을 추정하지 않는다.

원본 saver method spans는 서로 겹치고 일부 검증용 get도 포함한다. 합산한 값이 로컬 수행 시간보다 클 수 있으며 그 비율을 ‘DB 병목 비율’로 쓰지 않는다. serializer 측정은 실제 생성된 bytes를 관찰하며 두 번째 직렬화를 하지 않는다.

## 1. 단일 Tool의 stdout 크기

| 조건 | CP 개수 | 과거 포함 누계(MiB) | 최신(KiB) | 로컬 수행 평균 ms (최소~최대) |
|---|---:|---:|---:|---:|
| output_0 | 19 | 0.305 | 31.46 | 150.0 (115.5~211.7) |
| output_65536 | 19 | 0.335 | 46.99 | 179.8 (138.0~203.8) |
| output_1048576 | 19 | 0.335 | 46.99 | 143.6 (122.3~177.9) |
| output_16777216 | 19 | 0.335 | 46.98 | 153.1 (149.4~157.5) |

0은 Tool이 별도 로그를 출력하지 않는다는 뜻이며 compiler의 DTEST_OBSERVATION 요약은 계속 출력된다. 64KiB·1MiB·16MiB 모두 상태의 text가 16,000자 상한에 도달하고 tail에서 실제 summary를 읽었다. 따라서 CP 수는 19개로 같고 누계는 약 0.335MiB에서 거의 같았다. 원시 output은 full PV 파일에 남는다.

다만 output 읽기는 preview만 잘라 읽는 작업이 전부가 아니다. `_text_preview`는 checksum·size 검증을 위해 파일 전체를 1MiB chunk로 훑고 그 뒤 head/tail을 읽는다. 16MiB 조건의 reader 평균은 7.59ms다. 더 큰 파일·이미지/다중 representation·원격 PV 성능에는 비용이 늘 수 있다. 이 시험으로 GiB 출력 비용이나 다른 글자셋의 정확한 preview 상한을 예측하지 않는다. checksum 검증을 건너뛰는 변경은 하지 않았다.

## 2. 동일 20개 Tool의 Operation 분할

| 조건 | CP 개수 | 과거 포함 누계(MiB) | 최신(KiB) | 로컬 수행 평균 ms (최소~최대) |
|---|---:|---:|---:|---:|
| operations_1 | 19 | 0.549 | 106.64 | 224.1 (182.1~291.0) |
| operations_5 | 48 | 1.088 | 70.82 | 374.0 (307.4~421.9) |
| operations_20 | 153 | 3.042 | 65.17 | 821.5 (753.1~921.2) |

1→5→20회일 때 CP는 19→48→153개로 늘었다. 누계 저장량은 약 2배, 5.5배가 되고 로컬 수행 평균은 약 224→374→821ms였다. review 정책 차이를 포함한 현재 흐름의 비용이며, 실제 모델 5초를 더한 시간을 여기서 계산하지 않는다.

로그가 짧은 20 Operation에서는 checkpoints JSON+metadata가 약 77%다. 설치된 LangGraph 1.2.11에서 resume은 `versions_seen['__interrupt__']`에 모든 versioned channel을 기록하고 `should_interrupt`는 이를 비교한다. node input_schema는 읽기 경계이므로 이 tracking 자체를 줄이지 않는다. [설치 소스·SHA·발췌](library-evidence.json)를 보존했다. 라이브러리 JSON에서 version/interrupt 키를 임의 삭제하면 재개 의미가 달라질 수 있다.

기존 기본 decision_boundary는 이미 실행 가능한 승인 Tool을 묶는다. 중간 결과 판단이나 승인 경계가 필요하면 나눠야 한다. 성능만을 위해 every_tool 정책을 강제로 없애거나 결과가 필요한 판단을 미리 결정하지 않는다.

## 3. 큰 미리보기 × 여러 Operation

| 조건 | CP 개수 | 과거 포함 누계(MiB) | 최신(KiB) | 로컬 수행 평균 ms (최소~최대) |
|---|---:|---:|---:|---:|
| large_operations_1 | 19 | 1.157 | 417.50 | 273.2 (184.9~386.2) |
| large_operations_20 | 153 | 9.413 | 375.90 | 913.8 (836.2~959.0) |

Tool당 raw stdout 64KiB, 저장 text 최대16,000자를 적용했다. 최종 text는 20×16,000=320,000자지만, 전체 누계에서는 **observations blob+write만 약 6.59MiB**(20 Operation)였다. 1 Operation의 같은 channel은 약 0.63MiB였다.

원인은 `process_event`가 매번 `[이전 observations + 이번 facts]` 전체를 last-value로 반환하기 때문이다. 공식 saver가 바뀐 전체 channel blob과 node write를 각각 보존한다. 한 번에 20개 결과가 도착하면 한 번 저장하지만, 한 개씩 도착하면 길이1·2·…·20을 계속 저장한다. 한 툴의 제한만으로 Run 전체 누계가 일정해지지는 않는다. 이는 실측과 현재 코드 경로에서 확인한 누적 복사 문제이며 모든 상태 field가 항상 매 checkpoint마다 새 blob으로 쓰인다는 뜻은 아니다.

## 4. 반복 repair

| 조건 | CP 개수 | 과거 포함 누계(MiB) | 최신(KiB) | 로컬 수행 평균 ms (최소~최대) |
|---|---:|---:|---:|---:|
| repair_0 | 19 | 0.332 | 40.00 | 141.2 (137.9~144.8) |
| repair_1 | 27 | 0.509 | 43.64 | 182.5 (166.1~201.5) |
| repair_5 | 59 | 1.266 | 51.87 | 328.3 (323.0~334.2) |
| repair_10 | 99 | 2.321 | 62.07 | 625.8 (549.6~733.8) |

추가 repair마다 CP가 8개 늘었다. 최종 성공 값은 모두 sum=6, load 함수는 정확히 한 번이다. 실패/NOT_RUN 관찰과 승인 snapshot, 정확한 제출 body, receipt, repair history를 모두 보존했다. 10회일 때 CP JSON+metadata는 약 65%, channel에서는 repair_history가 약210KiB로 가장 컸다. 현재 기본 최대3회에서 이 10회 비용이 항상 발생하는 것으로 해석하지 않는다. source 수정·등록 Tool 재계획·custom code 레벨의 큰 후보는 후속 범위다.

## 5. 읽기·직렬화와 검증

- finalize wait에서 같은 tuple을 5회 읽고 checkpoint/pending_writes 일치를 확인했다. 20 Operation/큰 미리보기의 warm aget_tuple 평균 4.76ms, 1 Operation/큰 미리보기는 13.26ms였다. 전체 15표본 각각의 최소/최대는 summary에 있다. 마지막 body 크기·캐시·host 변동이 다르며 cold restart나 전체 graph 재구성 성능 순위로 쓰지 않는다.
- actual `_dump_blobs`+`_dump_writes` serializer 평균은 20 Operation 약3.53ms, 큰 미리보기/20 Operation 약3.44ms, repair10 약3.07ms다. 큰 누계 bytes만으로 Python serializer CPU가 현재 유일한 병목이라고 판단할 수 없다.
- 39회 모두 terminal 성공, 승인 snapshot 유지, 실제 최종 계산·summary 유지, Operation/finalize 수 일치, 성공 load 미재실행, 중복 완료/terminal event의 재제출 없음, text 상한, pool 연결 반환을 확인했다.
- 첫 pool을 종료한 후 새 pool에서 39개 완료 row를 실제 deserialize했다. **기존 pool을 닫고 중간 wait부터 graph를 재실행한 시험은 이번 harness에 포함하지 않는다.** 기존 064의 중간 wait 복구 검증은 별도 근거이며 이 큰 출력 조건을 모두 대신하지 않는다.
- 관련 실행·repair·결과 회귀 39 passed, skip0, 예상 inner-role durability 경고3개. 이번 run의 checkpoint 호출 error0. [로그](regression.log), [JUnit](regression.xml).
- 독립 검산 **796개**: cohort/repeat/thread isolation, row key 고유성, parent/write/blob 참조, SQL bytes 합계/channel별 합계, 실제 직렬화 blob bytes 일치, checkpoint 저장 호출 수·ID, budget/operation/finalize/관찰 개수 등을 다시 확인했다. [집계·검산](summary.json). gzip 원본을 다시 풀어 계산한 결과와 일치했다. 중복 blob·parent 누락·bytes 변조·조건 전체 누락의 네 대조군은 모두 거절됐다. [검산 영수증](validation.json).

## 판단과 다음 구현 후보

1. **observations의 증가분 저장을 첫 비교 후보로 둔다.** 실제 long flow에서 large_operations_20처럼 copy 비용이 우세한 조건을 겨냥한다. LastValue 전체 목록 반환을 단순 list reducer로 바꾸는 것만으로 저장량이 줄어드는 것은 아니다.
2. 공식 [DeltaChannel/storage 최적화](https://docs.langchain.com/oss/python/langgraph/checkpointers#optimize-checkpoint-storage)는 설치 버전에 존재하지만 **베타**다. delta-only write, 정기 snapshot, deterministic reducer가 필요하고 재구성 시 ancestor write 읽기 비용을 부담한다. 지금 도입하지 않았다. 먼저 별도 후보에서 새 요청 reset·failed/NOT_RUN/repair ordering·기존 checkpoint seed·HITL/Executor replay·pool 종료/새 graph 재개·중간 과거 checkpoint 읽기와 저장/읽기 시간을 A/B 검증한다. 저장 감소와 warm/cold 읽기 악화를 함께 보고 채택 여부를 결정한다.
3. 작은 관찰에서는 메타데이터 비중이 높다. side effect 없는 노드들의 super-step 통합 후보는 이후 검토하되 exact outbound body를 HTTP 전에 저장하는 경계, 접수 receipt·binding·HITL/event resume 경계를 먼저 명시한다. 더 적은 CP가 항상 더 좋은 복구 의미는 아니다. `durability='sync'`는 유지한다.
4. full output 검증은 파일 크기에 비례한다. 반복 확인이 실제로 문제인 workload가 확인되면 immutable ref 검증/요약 계약을 Executor와 함께 검토한다. 임의 trust cache나 checksum 생략은 후보가 아니다.
5. project memory와 raw execution evidence의 역할은 유지한다. approved source hash 외부화에는 불변 저장소 계약이 필요하고, 과거 pruning은 긴 작업·time travel·delta anchor 정책이 정해진 후 별도로 다룬다.

**판정: 제한을 명시하면 공유 가능.** 저장/기능 결과는 직접 검증했지만 시계열 유입·Pod/HPA·멀티 프로세스·운영 DB RTT/부하·실제 큰 데이터·LLM 추론·장기 대기는 미측정이다. 운영 처리량 목표/배포 한도를 확정하는 근거는 아니다. 기존 064 asyncpg Future 종료 경고의 원인 규명도 이번 무asyncpg 진단에서 해결한 것으로 표시하지 않는다.

## 재현과 자료

`profile_checkpoint_growth.py --fixture-dir <fresh-path> --output <fresh-json> --repeats 3`를 실행한다. 환경변수 `DTEST_GROWTH_PROFILE_DSN`은 **본인이 생성한 disposable** local PostgreSQL의 `agentic_checkpoint_test`만 넣는다. 이 도구는 기존 thread를 지우지 않지만 DB의 서비스용 여부를 이름만으로 확인하지 못하므로 서비스 DB를 재사용하지 않는다. Python 환경에는 프로젝트 dev 의존성이 필요하다. 측정은 CLI isolated process에서만 사용하고 monkeypatch telemetry를 serving process에 설치하지 않는다.

`scripts/diagnostics/analyze_checkpoint_growth.py <raw.json 또는 raw.json.gz> <summary.json>`으로 별도 재검산한다. 예비 실패/측정 준비 cohort는 [최종 로그](run.log)의 39회 원본과 구별했다. 재현 도구, 원본 SHA, 설치 라이브러리 SHA, SQL capture 쿼리는 모두 보존했다. 기존 서비스 설정을 재시작하거나 runtime flag를 바꾸지 않았다. 테스트 전용 PG 연결0 확인·컨테이너 제거 영수증은 [정리 기록](cleanup.json)에 있다.
