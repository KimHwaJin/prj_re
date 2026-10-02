# LLM을 제외한 현재 서비스 처리량 분석

## 요약

LLM 호출·prompt·Agent 흐름을 변경하지 않고 현재 API·Worker·DB·checkpoint·결과 이벤트·SSE를 분석하고, 확인된 처리 비용과 집중 요청 문제를 개선했다. **동일한 실행 한도16에서 50명 평균은 24.130→23.930초로 0.8% 차이**다. 큰 시간 단축은 실행 한도를32로 늘린 결과(2회 평균13.395초)이며 코드 개선만의 효과로 합산하지 않는다.

모델 지연을0으로 제거한 안정화 대조 시험에서는 50명 평균11.733→9.997초(14.8%), 사용자당 API CPU0.245→0.207초(15.7%)로 줄었다. 주 비교 측정29회·사용자 흐름856건이 완료되었고 변경 후 DB 연결 정리 경고가 없었다. 이는 로컬 유한 코호트의 결과이며 운영 최대 처리량 인증은 아니다.

## 측정 범위와 시간의 의미

평균 시간은 사용자가 새 Project·Session 생성 → Run 접수 → SSE 계획 대기 → 정책 편집 → SSE 재대기 → 승인 → SSE plan_approved 수신까지 걸린 시간이다. 모델은 고정 계획을 만들며 사용자당1회, 지연5000ms 또는0ms다. private invocation은 사용자당3개이고 public Run은1개다. 사용자 생각 시간 없이 일제히 시작한다. **Executor 제출 전 완료**이며 실제 분석 실행·모델 API·Phoenix·Jupyter 호출은 없다.

로그인·최초 사용자/기본 프로젝트 생성·완료 warmup은 계측 밖이다. 이후 API 요청은 실제 cookie/CSRF·권한 검사를 거친다. 사내 SDK의 직원 검증 verdict만 loopback fixture다. 자동 프로젝트 메모리 추출/모델 역할별 문맥 조회도 mock으로 우회되어 해당 경로의 비용은 포함하지 않는다.

단일 API 프로세스와 기존 로컬 Compose PostgreSQL/Redis를 사용한다. 실행 한도16/32, CRUD pool10·overflow0, checkpoint1~4, login Redis8, SSE0.5초, claim/cancel0.25초다. 매 시험마다 독립 UUID DB2개를 새로 만들고 migration한 뒤 삭제했다. 기존 DB·Streams·컨테이너를 초기화하지 않았다. 예비 한도4/16/32 스윕12회는 계측 미들웨어가 달라 주 비교29회와 합산하지 않았다.

## 같은 실행 한도에서는 차이가 작고, 한도32에서 대기가 줄었다

50명에서 같은 한도16의 큐 대기는 15.370→15.368초로 사실상 같다. Worker의 모델 제외 내부 시간은2.436→2.301초이고 DB pool checkout p95는71.805→50.563ms다. 사용자당 CPU는0.282→0.242초로 감소했지만 변경 전에는 SSE 연결 정리 경고가 있어, 순수 정상 경로 비용 비교에는 아래의 안정화 대조군을 함께 사용한다.

10명은 오히려7.438→7.621초였고30명은12.000→11.004초였다. 각1회이므로 작은 차이를 확정적인 개선/회귀로 단정하지 않는다. 현재 부하에서는 같은 한도의 큐 대기를 SQL 개선만으로 크게 없애지는 못했다.

50명 한도32의 두 측정 평균은13.357/13.433초이고 전체 종료16.247/16.638초다. 두 trial p95는16.206/16.613초다(합쳐 계산한 p95가 아님). 한도16 최종23.930초 대비 평균44.0% 단축이지만 이는 **설정 효과**다. 한도32의 큐 대기는4.468/4.388초이고 peak Worker32를 확인했다. sampled DB peak12개, RSS177.1/178.6MiB였다.

| 동시 사용자 | 변경 전 · 한도16 (초) | 변경 후 · 한도16 (초) | 변경 후 · 한도32 (초) | 같은 한도16 단축률 | 반복 |
|---|---|---|---|---|---|
| 1 | 6.884 | 6.831 | 6.806 | 0.8% | 각 1회 |
| 10 | 7.438 | 7.621 | 7.236 | -2.5% | 각 1회 |
| 30 | 12.000 | 11.004 | 10.768 | 8.3% | 각 1회 |
| 50 | 24.130 | 23.930 | 13.395 | 0.8% | 32 한도만 2회 |

## 모델이 없어도 SQL 처리 비용은 남아 있었다

짧은 SQL의 prepared statement cache를 연결별100으로 켜면 안정화 대조군11.733→10.258초였다. 이 상태에서 현재 계획 이벤트 저장이 이미 획득한 Run barrier를 같은 transaction에서 재사용하게 하여 최종2회 평균9.997초가 됐다. 이 마지막 단계의 평균 차이는2.6%이며 반복 수가 적다.

Worker advisory lock SQL은 사용자당31→24회(7회,22.6%)로 감소했다. 전체 SQL은401.02→389.33회로 약2.9% 감소했다. 현재 플로우도 여전히 사용자당 약390회 SQL을 사용하므로 모든 SQL 비용이 없어진 상태는 아니다. 원자적 log/event 저장, 메시지, sequence, replay, rollback 검사는 유지했다.

두 최종0초 시험에서 모델 제외 Worker 시간2.853/2.924초, pool p95 90.913/96.685ms를 기록했다. 모델을 제거하면 실제로 DB/CPU 경합이 드러나며, 모델5초 시험의 낮은 처리량을 전부 DB 문제로 해석하면 안 된다. 안정화 대조군1회·cache 대조군1회·최종2회인 순차 비교라 통계적 유의성이나 장기 용량은 주장하지 않는다.

| 50명 · 한도16 · 모델0초 | 반복 | 사용자 평균(초) | 전체 종료(초) | CPU초/사용자 | Worker 내부초/사용자 | SQL/사용자 | Worker advisory/사용자 |
|---|---|---|---|---|---|---|---|
| 안정화 대조군 / cache0 | 1 | 11.733 | 12.635 | 0.245 | 3.373 | 401.02 | 31 |
| SQL cache100 추가 | 1 | 10.258 | 11.148 | 0.214 | 3.001 | 398.06 | 31 |
| 현재 이벤트 batch 추가 · 최종 | 2 | 9.998 | 10.823 | 0.206 | 2.888 | 389.33 | 24 |


### 남아 있는 SQL·연결 경합

50명·한도32·모델5초 첫 시험의 SQL407.52회/사용자는 Worker213.00, API 쓰기99.00, SSE49.80, cancel 감시29.44, 큐 claim16.28회였다. 이 분포는 해당 한 시험이며 다른 부하에서 고정 비율은 아니다. 취소 감시는 실제로 사용자의 취소 여부를 확인하는 별도 조회다. 이를 줄이면 취소 반응시간도 달라지므로 이번 후보는 기존0.25초를 유지했다.

같은 시험의 pool checkout p95는61.99ms, event-loop lag p95는38.40ms/최대72.63ms였다. sampled lock waiter peak는0이었고 idle-in-transaction peak9는 관찰됐다. 짧은 조회도 transaction 사이에 잠깐 idle이 될 수 있으나 이번 sample은 지속 시간을 기록하지 않았으므로 장기 방치가 없다는 증거로 사용하지 않는다. 측정 종료 후 CRUD checkedout0을 별도로 검증했다.

따라서 남은 시간은 큐, SQL/CPU 경합, 전달 비용으로 나뉜다. replica/pool을 무조건 늘리는 것보다 실제트래픽에서 Worker 저장·취소감시·SSE·권한쿼리의 비용을 각각 확인할 여지가 남아 있다.

## CRUD는 조회 횟수를 줄이지 않고 처리 시간이 감소했다

CRUD는 사용자마다 Project 생성·Session 생성·Project 목록·Session 목록·Session 수정·Session 삭제·Project 삭제의7개 호출을 수행했다. 로그인과 모델 호출은 제외한다. 50명 평균0.811→0.623초, batch0.937→0.712초로 단축됐고 성공한 SQL 수는 사용자당47.06회로 같았다. 따라서 이 시험의 차이는 주로 SQL 처리 비용에 있으며, 삭제 정책이나 권한 검사를 생략한 결과가 아니다.

이 비교의 대조군도 로그인 pool·ASGI·SSE frame 안정화는 이미 적용한 상태다. CRUD의 원래59d690a 전체와 비교한 개선율로 표현하지 않는다. 각 조건1회, 1초 미만 시험이라50ms 차이 등 작은 값은 환경 변동의 영향을 크게 받는다.

| 동시 사용자 | 안정화 대조군 평균(초) | cache100 최종 평균(초) | 대조군 전체(초) | 최종 전체(초) | 평균 단축률 |
|---|---|---|---|---|---|
| 1 | 0.063 | 0.034 | 0.064 | 0.035 | 45.7% |
| 10 | 0.228 | 0.218 | 0.237 | 0.230 | 4.7% |
| 30 | 0.547 | 0.437 | 0.614 | 0.487 | 20.0% |
| 50 | 0.811 | 0.623 | 0.937 | 0.712 | 23.1% |

## 집중 요청의 로그인 포화와 SSE 연결 정리를 개선했다

원본0초50명 시험에서는45명만 완료하고5개의 HTTPStatusError가 발생했다. 실제 쓰기 요청에서503이 기록됐으나 에러 detail은 캡처하지 않았다. 원본 Redis pool은8개가 모두 사용 중이면 즉시 ConnectionError를 내는 구조였다. 개선은 최대8개를 유지하며3초 이내에서 자리를 기다린다. 실제 Redis 포화 회귀는 대기→해제 후 성공, 기한 초과503, 이후 회복과 소유 pool 종료를 검증했다. 모든503을 이 원인으로 독점 설명하지 않는다.

기존 request-id의 BaseHTTPMiddleware를 pure ASGI로 교체하여 task/cancellation 경계를 단순화했다. **이 변경만으로는 GC 연결 경고가 해결되지 않았다.** SSE subscriber가 pool checkout/SQL 중 끊길 때 전체 짧은 frame read를 별도 owned task로 끝내고 connection을 반납한 뒤 취소를 전달하는 변경이 추가로 필요했다. 최종 관련50명0초 시험2회와 변경 후 주 비교 전부에서 GC/traceback/error가0이고 checked-out CRUD 연결0·session owner0·recovery0을 확인했다. 반복취소 회귀도 통과했다.

변경 전5초1/10/30/50명은 요청 완료 검증은 통과했지만 GC 경고 문구가각4/20/52/80회 발견됐다. 이는 로그 문구 출현 수이며 유실된 연결 개수로 환산할 수 없다. 해당 경고가 있던 측정은 정상 대조군과 구별하여 보존한다. 실패 시험은 성공 평균29회에 섞지 않았다.

## SSE 간격을 줄이는 설정은 집중 부하에서 불리했다

SSE 합침 간격을0.5→0.1초로 줄이면1명·한도32·모델5초는6.806→6.040초로 상태 전달이 빨라졌다. 그러나50명에서0초·한도16은9.997→10.985초(각최종2회평균), 모델5초·한도32는13.395→14.060초로 더 느려졌다. 후자의0.1초는1회다. 빠른 전달과 높은 처리량 사이의 비용을 확인했으므로 최종 profile은0.5초를 유지한다.

SSE0.5초는 모든 stream이0.5초마다 DB를 조회한다는 뜻이 아니다. 기존 NOTIFY 기반 변경 감지·cache·cursor 조회를 유지하며15초 재조정과 별도로 전달을 합치는 간격이다. 잔여 client_delivery_and_other에는 CRUD/HTTP/SSE/Worker 구간 밖의 작업이 함께 들어 있어 SSE 지연 하나로 표기하지 않는다.

## 작은 풀을 유지하면서 실행 자리를 늘리는 설정 후보

설정 후보는 **단일 프로세스, Run concurrency32, CRUD pool10/overflow0, checkpoint max4, statement cache100, SSE0.5초, login Redis8**이다. cache의 코드 기본값0과 기존 dev/stg/prd는 유지하며 config.performance.yml을 명시적으로 선택하거나 해당 service block을 환경 YAML에 병합한다. YAML > env > 기본값 순서다. 자동 배포/컨테이너 재시작은 하지 않았다.

Run32개가32개 DB 연결을 계속 잡는 구조는 아니다. 모델/Executor 대기 중 연결을 반환하는 기존 구조를 유지한다. 현재 profile의 잠재 최대는 CRUD10 + checkpoint4 + official Store2 + SSE LISTEN1 =17개/프로세스다. 실제 Executor bridge max4와 event Worker pool은 켜졌을 때 추가된다. 시험에서 bridge는 비활성이고 Store 자동 읽기/쓰기 경로도 포화시키지 않았다.

추가 Uvicorn 프로세스나 Pod는 실행 슬롯·풀을 복제한다. replica 수를 조정할 수 없는 전제에서 pooler 없이 인스턴스 전체의 강한 연결 상한은 아직 보장하지 못한다. 실제 모델/Pod CPU quota/공유 DB 예산을 고려하여 한도16 또는32를 결정해야 하며, 이 서비스-only 시험으로 실제 모델32동시 호출을 허용했다고 해석하지 않는다.

## 검산과 회귀로 결과를 확인했다

Queue는 PostgreSQL agent_runs의 private invocation created_at→started_at 차이를 사용자별3구간 합산한다. Worker time은 정확한 시작/종료 구간, model time은 실제 mock sleep 구간이고 둘의 차이가 Worker 내부 시간이다. 중첩 trace span을 서로 더하지 않는다. interval sweep으로 peak Worker를 검증한다.

API process CPU는 warmup 이후 user+system CPU, RSS와 DB 연결/lock/idle transaction은약0.5초 간격 sample이다. DB/Redis/client CPU는 제외하고 memory는 cgroup이 아니다. peak sample은 짧은 최고치를 놓칠 수 있다. pool checkout에는 connection 생성/검사도 포함될 수 있다. throughput=사용자수/유한 batch 전체 시간이며 안정 유입률이 아니다. p95는 nearest-rank이다.

29회 raw에서856완료사용자·HTTP<400·고유session·flow마다3invocation/1model·retry0·session owner0·recovery0을 검증했다. 별도 검산기로377개 산술·압축 원문 hash 검사를 통과했다. 관련 회귀182개가 통과했고 체크포인터 없는 fixture의 기존 durability warning3개는 남는다. 설정 기본값·YAML 우선순위·범위 검증4개를 추가한 설정 검증42개도 통과했다(이전182개와 중복분은 합산하지 않는다). 원래 개발 checkout과 기존 서비스 환경은 유지한다.

## 운영 전체 처리량 확정에는 별도 검증이 필요하다

5초와0초 mock은 실제 모델 provider HTTP, create_agent metadata discovery, 문맥 준비/메모리 추출, prompt 생성 전체 비용을 재현하지 않는다. 최초 계획 후 편집·승인 경로이며 실제 Executor 제출·Streams 결과·후속 설명·보고서·자동 project_memory의 처리량은 이번에 측정하지 않았다. 이전055의 실제 연계 성공은 이 경로의 용량 측정으로 대신 사용할 수 없다.

유한 일제 유입·최대50명·대부분조건1회·로컬서버라는 한계가 있다. 신뢰구간, 운영 HPA, CPU quota, 실제 model rate limit, 여러 Pod의 DB전체예산, 초장기 실행·대용량 데이터 처리량은 확정하지 않는다. 원래total_merge_v1의4-call시나리오와 이번1-callmock을 섞어 리팩토링 전체의 개선율로 주장하지 않는다.

## 다음 적용·측정 방향

- 이 profile을 배포 전 설정 후보로 사용하고 실제 Pod 제한에서16/32를 비교한다. 모델 호출 최적화는 기존 보류를 유지한다.
- 운영의 queue p95, CPU/loop lag, DB pool wait·전체 연결, auth503, SSE 정리 경고를 함께 본다. queue만 보고 pool/프로세스를 늘리지 않는다.
- 현재 플로우의 남은 SQL은 사용자당약390회다. 추가 최적화는 SQL 목적별분포와 실제동작을 대조하여 권한·멱등·원자성을 보존하는 범위에서 진행한다. 무작정 DB풀확대는 권장하지 않는다.
- Executor 실제 제출·이벤트·결과 및 메모리/후속답변의 service-only 용량은 별도 연계 부하시험으로 확인한다. 미구현 Dataset Registry·Workflow CRUD는 기존 보류다.

## 운영 확정을 위해 필요한 정보

실제 Pod의 CPU/memory 제한과 모델 동시 호출 허용량은 얼마인가? 배포시 같은 PostgreSQL을 보는 API·Agent·Executor·배치와 플랫폼 replica 총수가 얼마나 되는가? 실제 트래픽에서 신규 계획, HITL 수정, 후속 질문, 상태 구독의 비율은 어떤가? 이 값들이 있어야 현재 후보를 최종 운영 용량으로 확정할 수 있다.

재현·설정: [benchmark 안내](../../../scripts/benchmarks/service_throughput/README.md), [적용 가이드](../../service-throughput-settings.md). 근거: [전체 결과](results.json), [원문 hash](manifest.json), [검산](independent-verification.json), [로그 품질](log-quality.json), [실패 대조군](failed-control.json).
