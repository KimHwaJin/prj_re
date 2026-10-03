# Executor 연계 서비스 처리량 분석

## 요약

LLM 호출·prompt·Agent 로직을 바꾸지 않고 Executor 연계까지 서비스 경로를 개선했다. **50명·Agent 한도32·최초 모델 대기5초의 같은 조건에서는 평균 23.622→22.686초 (4.0% 단축)**였다. 각2회 평균이며 운영 용량이나 통계적 유의성을 확정하는 수치는 아니다.

확실히 구분해야 할 문제는 빠른 Executor 결과의 세션 인계다. 모델0초·50명 대조군은 결과 재개39회를 유예하여 평균 38.821초였다. 개선 후는 유예 0회·평균 19.584초로 끝났다. **재전달 지연 제거의 효과를 정상 경로 SQL 개선 효과와 합산하지 않는다.** 작은 풀을 유지하고 Agent 한도32·Event ingress/dispatch4를 설정 후보로 제공한다.

## 시간 비교는 리포트 완료까지의 동일 플로우다

전체 시간은 Project/Session 생성 시작 → 새 Run의 계획 제시 → 정책 편집 → 최종 승인 → 실제 HTTP Executor 제출/continue/finalize → Redis 결과 이벤트 → Event Worker 그래프 재개 → Markdown 리포트 success SSE 수신이다. 056의 plan_approved 종료보다 긴 시나리오이므로 두 보고서의 절대 시간을 직접 비교하지 않는다.

사용자당 public Run1개, private Agent invocation3개, MULTI Operation2개, Executor HTTP3개, 의미 있는 결과 재개3개, 관찰4개다. 최초 계획 mock만5000ms 또는0ms이며 review/report mock은0ms다. 사용자 생각 시간0·유한 일제 유입·1/10/30/50명, 단일 로컬 API 프로세스다. 로그인·완료 warmup은 측정 밖이다.

현재 cookie/CSRF·권한·CRUD·checkpoint·DB Inbox/Outbox·Redis Streams·manifest 읽기·결과 저장·SSE는 실제로 실행한다. 직원 검증 verdict, 모델 및 Executor 코드 실행/출력은 fixture다. mock Executor는 제출 코드를 실행/import/eval하지 않고 작은 합성 stdout과 checksum manifest를 작성한다. 따라서 본 부하 시험은 실제 Jupyter 계산·대용량 데이터·모델 provider 용량 시험이 아니다.

## 같은 Agent 한도에서 코드 효과를 비교했다

**모델5초 조건의 변경 전/후를 Agent 한도별로 비교했다.** 그림의16/32는 API 프로세스 수가 아니라 한 프로세스의 Agent 실행 자리다. 같은 한도의 두 색을 비교해야 코드 효과를 읽을 수 있다. 50명·32는 양쪽 각2회 평균이고 다른 조건은1회다. p95 표는 trial별 최소–최대이며 합쳐 계산한 p95가 아니다.

1명에서는 idle Router/Outbox 기다림을 깨워 결과 전달이 빨라진다. 큰 코호트에서는 DB/CPU/SSE 경합과 다른 사용자 결과 대기가 남는다. 한도32만으로 큐가 사라지는 것은 아니며, 더 많은 Event 자리로 같은 수준의 개선을 보장할 수도 없다. 실행 한도 확대와 반복 저장 감소를 서로 다른 변화로 평가한다.

| 동시 사용자 | Agent 한도 | 변경 전 평균 초 | 변경 후 평균 초 | 단축 % | 양쪽 반복 | 전 p95 범위 | 후 p95 범위 |
|---|---|---|---|---|---|---|---|
| 1 | 16 | 10.101 | 6.765 | 33.000 | 1 | 10.101–10.101 | 6.765–6.765 |
| 1 | 32 | 10.041 | 6.736 | 32.900 | 1 | 10.041–10.041 | 6.736–6.736 |
| 10 | 16 | 10.775 | 8.706 | 19.200 | 1 | 11.121–11.121 | 8.844–8.844 |
| 10 | 32 | 9.919 | 8.648 | 12.800 | 1 | 10.148–10.148 | 8.740–8.740 |
| 30 | 16 | 15.818 | 13.184 | 16.700 | 1 | 17.344–17.344 | 16.234–16.234 |
| 30 | 32 | 16.574 | 15.656 | 5.500 | 1 | 17.114–17.114 | 16.163–16.163 |
| 50 | 16 | 33.684 | 32.010 | 5.000 | 1 | 34.761–34.761 | 33.053–33.053 |
| 50 | 32 | 23.622 | 22.686 | 4.000 | 2 | 24.681–24.806 | 23.582–23.867 |

## 반복 투영을 줄여 DB·CPU 비용을 낮췄다

**50명·한도32의 사용자당 CRUD SQL은 676.11→583.08회 (13.8% 감소)**했고, API 프로세스 CPU는 0.397→0.378초였다. 여기의 CRUD SQL은 asyncpg만 포함하며 checkpoint/Event/bridge의 psycopg SQL 총량은 아니다. 아래 시간들은 겹치는 작업이므로 합쳐 E2E 비율로 만들지 않는다.

기존 values stream은 노드가 넘어갈 때마다 누적 public events를 다시 서비스 DB에 투영했다. 새 InvocationProjection은 현재 호출에서 성공적으로 commit된 동일 내용만 생략하며 새 이벤트·변경 payload는 기존 DB 중복 제거로 보낸다. 첫 상태·새 호출·최종 receipt 확인 및 복구 snapshot은 그대로 전체 투영한다. 최초 요청·사용자 resume·Executor 결과 resume에 같은 범위를 적용했다.

checkpoint durability=sync, receipt, 메시지/log/event 원자성, unique key, 세션 소유권과 최종 복구 확인은 유지한다. custom dispatcher/legacy graph는 전체 상태 전달 계약을 유지한다. 이 최적화는 호출 밖·다른 Pod에 전달되는 결과 캐시가 아니고 DB 연결도 보유하지 않는다.

| 조건 | CPU초/사용자 | CRUD SQL/사용자 | 결과투영 SQL/사용자 | Event 대기 ms | Handler ms | Agent 큐합 ms |
|---|---|---|---|---|---|---|
| 변경 전 | 0.397 | 676.110 | 224.000 | 2892.327 | 289.019 | 4794.888 |
| 변경 후 | 0.378 | 583.080 | 176.420 | 2626.611 | 303.128 | 4563.244 |

## 빠른 결과는 세션 인계 전에 와서 재전달을 기다렸다

**모델0초·50명에서는 변경 전 150개의 성공 결과 처리 외에 DeferEvent39회가 있었다.** 요청 자체는 모두 완료했고 Run 재실행 횟수는1이었다. 그러나 빠른 결과가 API 실행의 세션 반납 전에 도착하면 기존 소비자는 즉시 유예하고 Redis PEL에 남긴다. reclaim idle 기본30초 때문에 재전달까지 긴 시간이 붙을 수 있다. 성공한 handler 시작 기준의 이벤트 대기에는 이 시간이 포함된다.

변경 후는 API owner 또는 실행 중/대기 중 API task에 한해서 재확인 대기 예산1초 안에서50ms 간격으로 확인한다(개별 SQL 시간은 별도다). 매번 DB context를 닫고 기다리며 owner를 빼앗지 않는다. 다른 Event owner·recovery·비활성 리소스는 기존처럼 즉시 유예한다. 기한 내 반납되지 않으면 기존 재전달 경로로 돌아간다. 같은 세션의 사용자 입력 잠금은 그대로다.

이0초 대조군은 정상 비용 비교로 표시하지 않는다. 정상 완료된 HTTP와 조용한 이벤트 소비는 별개의 품질 지표이며,39회를 숨긴 성공률만으로 개선을 평가하면 원인이 가려진다. 각1회라 유예 확률이나 운영 개선율을 일반화하지 않는다.

| 조건 | 평균 초 | p95 초 | 유예 횟수 | Handler 시도 |
|---|---|---|---|---|
| 변경 전 · 재전달 발생 | 38.821 | 46.629 | 39 | 189 |
| 변경 후 · 정상 재개 | 19.584 | 20.486 | 0 | 150 |

## commit된 local 작업은 idle scan을 깨운다

Router/Outbox의 유휴 scan 간격은 기존0.2초→최대2초 backoff를 유지했다. 같은 프로세스에서 처리 가능한 원본 event가 durable commit되거나 command가 완료되면 다음 단계의 asyncio.Event를 깨우고20ms 안에서 신호를 합친다. Step/progress처럼 handler가 없는 이벤트마다 깨우지는 않는다. 에러 중에는 기존 backoff를 유지한다.

Executor가 binding 등록 전에 모든 결과를 발행한 경우도 있어, binding transaction commit과 연결 반환 뒤 별도 local 신호를 보낸다. 신호 자체에는 업무 데이터가 없고 추가 DB/Redis 연결도 없다. durable Inbox/Outbox가 실제 작업의 기준이며 다른 Pod에서 들어온 변경·재기동은 기존 주기 scan으로 발견한다. 따라서 모든 Pod에서 즉시 재개된다는 의미는 아니다.

## Event 자리를 무조건 늘리지 않고 작은 풀을 유지한다

Event dispatch4/8/16 비교는 **최종 공통 투영 전의 중간 후보**에서50명·Agent32·모델5초로 각1회 수행했다. 4의 두 trial 평균23.624초,8은 23.091초,16은 24.311초였다. 8의 약2% 차이만으로 Event 한도를 늘리지 않으며16은 더 느렸다. 이를 최종 코드의4/8/16 sweep이나 확정적인 최적점으로 표현하지 않는다.

opt-in config.performance.yml은 Agent32, Event ingress4/dispatch4, EW pool4, CRUD10/overflow0, checkpoint max4, SSE0.5초다. EW pool 설정은 Event Worker와 제출 bridge의 서로 다른 풀에 각각 적용된다. 잠재 DB 예산은 CRUD10+checkpoint4+official Store2+SSE LISTEN1+Event4+bridge4=25개/프로세스이며 Pod·프로세스 증가 시 복제된다. 시험의 checked-out0은 열린 idle 연결0을 뜻하지 않는다.

## Executor 대기 중 실행 자리와 점유 연결을 반환했다

10명의 모든 Run이 WAITING_EXECUTOR에 도착한 뒤 첫 결과를5초간 보류했다. 약0.27초 간격20개 표본에서 Agent 실행0·Event 실행0이고 모든 psycopg 연결이 pool에 반환된 상태였다. CRUD checkout은13개 표본에서0,7개에서1이었다. 같은 보류 구간의 DB active/idle transaction 표본은0이며, 실행10건이 각자 연결을 계속 점유한 모습은 아니다. checkout owner를 기록하는 별도 반복에서는20개 표본 모두 CRUD checkout0이었다. 첫 시험의1개 checkout은 소유자를 기록하지 않아 정확한 작업을 확정할 수 없다. 두 시험의 실제 값을 모두 남기며, 모든 표본이0이었다고 요약하지 않는다. sampled finite hold이며1주 대기나 Pod 재배포까지 검증한 것은 아니다.

실제 로컬 Executor는 변경 전/후 각각1건, 총2건을 비교했다. Jupyter의 실제 parquet로 MULTI2 Operations·continue·명시적 finalize·관찰4개·리포트 ready가 완료됐다. 시간은 11.495→6.232초이나 각1건 기능 대조라 계산 처리량 개선으로 주장하지 않는다. 자신의 결과 디렉토리를 보존하고 기존 DB/Stream/컨테이너는 초기화하지 않았다.

## 고유 Run·결과·정리·원문 검산으로 확인했다

보고서에는 총26 trial·686 완료 사용자 흐름을 보존한다. 주 비교는 모델5초·동일EW4이며0초 유예 대조군·5초 보류·실제Executor·중간EW sweep을 따로 표시한다. 최종 mock측정은 HTTP<400, privateRun3n/publicRun n, 모델n, review/report각n, 성공 Event재개3n, Runattempt1, CommandDONE, reportready, owner/recovery/pending/CRUDcheckout0을 검사했다.

평균은 사용자별 전체 wall time, batch는 마지막 사용자 종료까지, 처리율은n/batch다. queue는 private invocation3개의 created_at→started_at 합, event wait는 원본 XADD 완료→성공 handler 시작이다. publication 경계는 XADD 직후 DB outbox 갱신 시작이므로 순수 네트워크 구간으로 해석하지 않는다. p95는 선형 보간 percentile이다.

CPU는 계측 API process의 user+system이며 DB/Redis/client/mock CPU는 제외한다. RSS/DB는약0.5초 표본이다. startup/warmup을 포함하는 psycopg pool누계를 측정 SQL로 표시하지 않는다. 원문 gzip/hash 및 별도 검산기로 산술을 확인하고 SQLite의 실행된 GROUP BY로 반복 평균을 작성했다. 전체 회귀919개에서916개통과·fixture mismatch3개가 나왔으며 수정 후 관련112개가 모두 통과했다. 추가capture검증2개까지 총921개 고유 항목을 다뤘다(중복 test run 합산하지 않음).

## 로컬 서비스 결과를 운영 전체 용량으로 환산하지 않는다

모델/provider HTTP/create_agent metadata·project_memory 자동 문맥 읽기/갱신과 후속질문·보고서 재작성의 처리량은 포함하지 않는다. 실제 Executor 대용량 계산·외부 데이터레이크·MinIO·수십GB 파일·초장기 결과·HPA·CPU quota·멀티Pod 인계는 이 로컬 부하의 범위를 벗어난다.

대부분 조건1회·50명32만2회이며 순차 측정이라 신뢰구간/안정 도착률/통계적 유의성은 주장하지 않는다. Event wake는 local hint라 다른 Pod 처리의2초 fallback이 남는다. 성공이 빨라진0초 결과에는 유예 재전달 제거가 포함돼 정상5초 코드 효과보다 크다. 운영한도는 실제모델/Pod CPU와공유DB예산에서 재검증해야 한다.

## 다음 측정·적용 방향

- Agent32·Event4·작은 DB풀 profile을 배포 후보로 유지하되 실제 Pod 자원 제한에서16/32를 대조한다. 이 작업은 자동 적용·배포하지 않았다.
- 처리량 관점의 다음 범위는 후속 설명/보고서 및 project_memory 읽기·자동 갱신의 서비스 비용이다. 모델 호출 횟수/prompt 최적화는 기존 보류를 유지한다.
- Event0초유예·queue p95·pool wait·loop lag·CPU와공유DB 전체연결을 함께 본다. 큐가 길다는 이유만으로 Event/DB 한도를 늘리지 않는다.
- Dataset Registry·Workflow CRUD·보고서 Artifact 등록·폭넓은 운영 보완은 기존 미확정/후순위 범위를 유지한다.

## 운영 확정에 필요한 정보

실제 Pod CPU/memory 한도와 운영 유입률은 얼마인가? 신규 계획·HITL 편집·후속질문·memory 사용비율은 어떤가? 모든 replica/API·Agent·Executor·배치가 같은 PostgreSQL에 붙는 실제 연결 예산은 얼마인가? 이 값들이 있어야 현 profile을 운영 용량으로 확정할 수 있다.

[전체 결과](results.json), [원문 hash](manifest.json), [로그 품질](log-quality.json), [독립 검산](independent-verification.json), [SQLite 요약](cohort-summary.sql), [대기 확인](waiting-proof.json), [재현 안내](../../../scripts/benchmarks/executor_throughput/README.md), [설정](../../service-throughput-settings.md).
