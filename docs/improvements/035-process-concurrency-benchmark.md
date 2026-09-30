# 035 — API 프로세스·Run 동시 실행 수 비교

- 상태: 21회 실측·독립 검산·보고서 완료 / 베이스 병합 완료·배포 미수행
- 브랜치: `feature/process-concurrency-benchmark`
- 서비스 기준 소스: `c3534f0` (034 완료)
- 날짜: 2026-09-30

## 목적과 범위

Run 컨커런시 4/8/16과 Uvicorn worker 1/2/4개를 비교한다. 1×4, 1×8, 2×4, 1×16, 2×8, 4×4 각각 사용자 10/30/50명의 유한 동시 시작 batch를 수행한다. API는 실제 Uvicorn shared socket multiworker이며 PostgreSQL queue·session barrier·checkpointer를 공유한다. 로컬 LLM HTTP mock은 호출당 5초, 사용자당 4회. Workflow 승인 대기까지 진행하고 Executor 제출은 제외한다. Agent/서비스 운영 코드는 변경하지 않는다.

Service DB pool은 프로세스당 10/overflow 0, checkpoint 최대 4, bridge 4다. 프로세스 증가에 따른 pool 복제 비용을 함께 측정한다. 이 조건은 총 DB pool budget이 같은 비교가 아니라 같은 프로세스별 설정으로 배포하는 방식의 비교다. Pod 수/리소스 제한/운영 설정은 변경하지 않는다. 로컬 개발 머신과 Docker PostgreSQL의 결과를 Kubernetes 권장값으로 확정하지 않는다.

## 지표·검증 계획

완료 평균/p95/처리율·큐 대기·모델/내부 시간, 모든 worker process CPU시간의 합, 같은 시점의 RSS 합, pool 획득 지연, loop lag, PostgreSQL 연결/active/idle transaction/lock waiter 표본을 수집한다. 부모 supervisor RSS는 별도로 기록한다. sampled maxima는 순간 최고치를 놓칠 수 있다. SQL 실행 시간에는 서버 실행·lock·전송 대기가 함께 포함된다.

등록된 모든 worker의 계측을 직접 수집한다. 모델/Run/stage 수, process별 컨커런시 상한, DB 상태·메시지/log 수, 종료 상태와 표본 수를 검산한다. 실패/검열 trial은 성공 평균에 포함하지 않는다. 원본·분석 코드·실행 조건·재현 절차와 보고서를 남긴다.

## 완료 결과

6개 조합 × 10/30/50명 = 18회에, 총 16자리·50명 조건을 역순으로 3회 더 수행했다. 본 측정 21회, 사용자 690명, Run 실행 구간과 모델 호출 각각 2,760회 모두 정상 완료했다. 별도 2×4·10명 smoke는 본 비교에서 제외했다. 100명 측정 및 Executor 제출은 수행하지 않았다.

평균 사용자 완료 시간(초):

| 프로세스×컨커런시 | 10명 | 30명 | 50명 |
|---|---:|---:|---:|
| 1×4 | 48.25 | 150.72 | 254.90 |
| 1×8 | 28.83 | 72.56 | 131.87 |
| 2×4 | 29.10 | 71.57 | 127.01 |
| 1×16 | 24.66 | 38.18 | 70.24 |
| 2×8 | 24.71 | 37.24 | 65.64 |
| 4×4 | 24.57 | 37.32 | 63.17 |

50명·총 16자리 조건은 2회 평균이고 나머지는 1회다. p95는 반복별 p95의 평균이며 pooled p95가 아니다.

50명 자원 비용(최고치는 반복별 표본 최고값의 평균):

| 조합 | API RSS MiB | DB 연결 | API CPU 초/사용자 | pool 획득 p95 ms |
|---|---:|---:|---:|---:|
| 1×4 | 239.7 | 13.0 | 0.772 | 2.6 |
| 1×8 | 244.7 | 14.0 | 0.605 | 26.3 |
| 2×4 | 454.6 | 26.0 | 0.634 | 2.6 |
| 1×16 | 257.8 | 13.0 | 0.508 | 90.1 |
| 2×8 | 461.6 | 26.5 | 0.544 | 27.0 |
| 4×4 | 829.4 | 53.0 | 0.628 | 3.0 |

### 판단

- 같은 1프로세스에서 컨커런시 4→16: 평균 254.90→70.24초, 약 72.4% 감소. 큐 대기가 231.35→45.71초로 줄고 모델 호출 합계는 약 20초로 같다. 큰 차이는 실행 자리 증가에서 발생했다.
- 같은 16자리에서 1×16→4×4: 평균은 약 10.1% 짧지만 RSS는 약 3.2배, DB 연결은 약 4.1배다. 2×8·4×4의 순위는 반복별로 바뀌었다. 모든 구간에서 다중 프로세스의 우월성을 확정하지 않는다.
- 이 조건에서는 단일 프로세스의 컨커런시 상향을 자원 효율 우선 후보로 삼는다. 16은 시험한 상한이며 최적값 또는 운영 한계가 아니다. 1×16에서 pool 획득 p95 약 90ms로 증가했으므로 무제한 확대 근거가 아니다.
- DB lock waiter는 표본에서 0이었다. 짧은 잠금 대기 부재를 증명하지 않는다. idle-in-transaction 표본은 짧은 정상 트랜잭션도 포함하므로 발생 자체를 누수로 판단하지 않는다.
- 실제 LLM의 동시 요청 한도, Pod CPU/memory 제한 및 지속 유입 조건은 미검증이다. 운영 worker·컨커런시 기본값은 변경하지 않았다. 새 Agent 흐름 변경 후 용량을 재검증한다.

## 변경과 검증

`process_scaling` 벤치마크 실행기·분석기·검산기·보고서 생성기를 추가했다. 실제 Uvicorn 멀티프로세스와 프로세스별 직접 계측을 사용한다. 기존 runtime_profile server는 main guard를 추가하고 분석기는 total_slots를 읽도록 일반화했다. Agent/API 운영 소스 변경은 없다.

- 계측 분석기 검증 9개 통과. 실제 smoke 원본과 process 누락·PID 중복·Run 누락/중복·상한 초과·자원 표본 누락·CPU 합계 오염을 검증했다.
- 21개 원본의 사용자 평균/p95·처리율·큐 대기·CPU·RSS·DB 최고 표본·호출 수 및 HTML 집계 표를 별도 계산으로 검산했다.
- 이전 034의 4자리 원본에 대해 기존/변경 분석기의 전체 결과가 동일함을 확인했다.
- HTML artifact 구조 검증은 통과했다. 설치된 Chrome을 이용한 렌더링 검증은 2회 timeout으로 미완료이며, 기본 도구에서도 headless-shell 미설치가 확인됐다. [검증 판단](../reports/process-concurrency-benchmark-2026-09-30/validation.json)에 제한을 남겼다. 수치 검산과 브라우저 표시 검증을 구분한다.
- 서비스 코드 미변경으로 전체 서비스 회귀 테스트는 이번에 재실행하지 않았다. 034의 전체 회귀 결과를 이번 검증으로 재표기하지 않는다.
- 전용 PostgreSQL 컨테이너 `dtest-process-scaling-test` 및 해당 볼륨을 삭제했고, 벤치마크 프로세스는 정상 종료했다. 기존 개발 서비스는 재시작/변경하지 않았다.

## 산출물

- [주 보고서 HTML](../reports/process-concurrency-benchmark-2026-09-30/report.html)
- [집계 결과](../reports/process-concurrency-benchmark-2026-09-30/results.json), [원본 해시 목록](../reports/process-concurrency-benchmark-2026-09-30/manifest.json)
- [독립 검산](../reports/process-concurrency-benchmark-2026-09-30/independent-checks.json), [환경](../reports/process-concurrency-benchmark-2026-09-30/environment.json)
- [재현 절차](../../scripts/benchmarks/process_scaling/README.md)

작업 commit `a1e2f82`를 2026-09-30 사용자 요청으로 `feature/refactor-base`에 fast-forward 병합했다. 028~035를 함께 반영했으며 충돌·추가 코드 변경은 없었다. `feature/process-concurrency-benchmark` 브랜치는 보존했다. 원격 push·운영 배포는 수행하지 않았다.
