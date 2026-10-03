# 서비스 CPU 비용 진단·워커 조회문 재사용 비교

LLM 지연을 제외한 서비스 CPU를 분리해 측정하고, 워커의 작업 점유 조회문을 매번 만드는 비용을 줄였다. 아래 숫자는 프로파일러를 끈 동일 조건의 유한 burst 시험이다. 실제 모델·Executor 계산·운영 Pod 처리량을 뜻하지 않는다.

## 비교 기준

- 변경 전 `f2009d1e74d30ac0e444223628bf7e6455ba9092`: 065 전체 값 저장 + 검증된 068 이력 경로 수정. 보류된 066 증가분 저장은 제외.
- 변경 후 `2a815c86ddacaf0db7cfe12b06e2f776f9fd6a52`: 운영 소스는 `src/api_service/runs/commands/claim.py`만 변경. SQL을 한 번 구성하고 namespace·현재 시각은 매 호출 새로 bind한다.
- API/SSO fixture/CRUD PG/Checkpoint PG/Redis Streams/공통 Worker/HTTP Executor fixture/HITL/SSE/리포트는 실행. LLM은 고정 응답·지연0, Executor는 합성 출력이며 제출 Python은 실행하지 않는다. 원본 manifests와 출력 무결성 검사는 유지.
- 총 실행 한도20, CRUD pool10/overflow0, checkpoint pool4, event pool4, SSE0.5초, 취소 확인0.25초, notify on. 서비스 설정·호출 횟수·승인·순서·잠금·durability는 동일.
- 표준: 4 Tool/2 Operation/모델4회. 진단용 대형: 20 Tool/20 Operation/각 출력64KiB/모델23회.
- 1·10·30명 각 전후1회, 50명 전후3회. 순차 실행, 10명 및 50명2회차는 전후 순서를 뒤집었다. 한 대의 로컬 Mac/Docker이며 CPU·메모리 제한/지속유입/HPA 시험은 아니다.

## 프로파일러 없는 시간·CPU 비교

| 사용자 | 전/후 반복 | 전체 완료 전→후(초) | 시간 감소 | 사용자 평균 전→후(초) | 서비스 CPU 전→후(초) | CPU 감소 |
|---|---|---|---|---|---|---|
| 1 | 1 | 1.716 → 1.691 | 1.4% | 1.715 → 1.691 | 0.410 → 0.362 | 11.9% |
| 10 | 1 | 4.471 → 4.450 | 0.5% | 4.293 → 4.223 | 3.842 → 3.864 | -0.6% |
| 30 | 1 | 13.350 → 12.764 | 4.4% | 12.801 → 12.158 | 12.834 → 12.296 | 4.2% |
| 50 | 3 | 21.884 → 21.200 | 3.1% | 20.836 → 20.162 | 21.142 → 20.471 | 3.2% |

전체 완료는 측정 cohort 시작부터 마지막 사용자 terminal SSE까지, 사용자 평균은 개별 flow의 완료 시간 평균이다. CPU는 API 프로세스의 reset부터 owner/CRUD pool drain 표본까지의 process_time이며 서버·fixture·DB CPU를 모두 합한 값이 아니다. 50명은 세 trial 값의 산술평균; p95는 각 trial의 nearest-rank이며 원본에 별도 기록했다. 음수 감소율은 느려짐/CPU 증가를 뜻한다. 유한 burst의 users/elapsed를 지속 처리량으로 환산하지 않는다.

50명 전체 완료 범위: {"before": [21.566684833960608, 22.312722583999857], "after": [21.14699445804581, 21.255114750005305]}. 작은 표본으로 통계적 유의성·운영 개선율을 확정하지 않는다.

## CPU 진단과 계측 비용

표준 라이브러리 cProfile + thread_time을 사용했다. 메인 스레드와 ThreadPoolExecutor 작업을 따로 수집해 DB/HTTP 대기 wall time과 구분한다. exclusive self CPU만 그룹 합산하며 cumulative CPU는 중복 합산하지 않는다. async/greenlet 중첩의 cumulative 값으로 상위 함수별 점유율을 단정하지 않는다. profiler instrumentation/native/기타 스레드의 residual은 미귀속이며 native 라이브러리 내부 call stack은 별도 분해하지 않는다.

- standard10-profile: process CPU 9.426초, main 9.163초, offload 0.186초. SQLAlchemy main self 4.163초 (44.2%/process). cache-key 재귀 calls 94,466.
- large1-profile: process CPU 3.242초, main 3.177초, offload 0.049초. SQLAlchemy main self 1.195초 (36.9%/process). cache-key 재귀 calls 21,605.
- after-standard10-profile: process CPU 8.793초, main 8.542초, offload 0.180초. SQLAlchemy main self 3.835초 (43.6%/process). cache-key 재귀 calls 83,465.
- after-large1-profile: process CPU 3.006초, main 2.942초, offload 0.049초. SQLAlchemy main self 1.040초 (34.6%/process). cache-key 재귀 calls 16,419.

| 조건 | profiler off 완료 | on 완료 | on 시간 증가 | off CPU | on frozen CPU |
|---|---|---|---|---|---|
| standard10-off | 4.725 | 9.766 | 106.7% | 4.003 | 9.426 |
| large1-off | 4.218 | 5.870 | 39.2% | 1.473 | 3.242 |
| after-u10-r1 | 4.450 | 8.945 | 101.0% | 3.864 | 8.793 |
| after-large1-off | 3.697 | 5.369 | 45.2% | 1.303 | 3.006 |

각 조건1쌍이므로 계측 비용의 안정적인 평균은 아니다. on CPU는 export 전에 고정한 process CPU이고 off CPU는 drain 표본 기준이다. 진단과 속도 시험을 섞거나 overhead를 사후 차감하지 않는다.

## 유지한 처리와 검증

실제 PG 회귀17개(skip0): 동시 점유1건, 동일 세션 순서, 다른 세션 진행, gap·중복, claim 후 재시작, owner 변경, user/event 공통 한도, namespace 분리 및 현재 시각 갱신. 쿼리 결과·Run·소유권·DB session을 메모리 캐시하지 않으며 매번 SELECT/FOR UPDATE SKIP LOCKED를 수행한다. 공개 응답·LLM 호출·checkpoint 쓰기 포맷·승인 경계는 바꾸지 않았다.

원본 trial 총 19회(진단/overhead 포함). 모든 시도는 attempts.json에 보존하고 실패를 성공 평균에서 제거하는 방식으로 숨기지 않는다. 각 flow의4/20 observations, HTTP 제출·operation·finalize, 최종 보고서, common command DONE, owner/recovery/Inbox drain을 검산한다. fixture warmup execution은 measured session으로 필터하며 measured 모델·SQL 카운터는 reset 이후다.

## 재현·남은 범위

`verify.py`는 원본 gzip/SHA·source manifest·결과·호출·집계 비교를 다시 계산한다. `measurements.json`, `comparison.json`, `profiles.json`, `attempts.json`, `source-audit.json`, `regression.log`와 `raw/`를 참조한다. 재현 명령은 같은 고정 소스와 전용 localhost63372/63373 scratch 자원을 써야 한다. 기존 DB나 실제 Executor를 지정하지 않는다.

```sh
PYTHONPATH=src .venv/bin/python scripts/benchmarks/worker_e2e/run.py \
  --database-url postgresql+asyncpg://postgres:<scratch-password>@127.0.0.1:63372/postgres \
  --redis-url redis://127.0.0.1:63373/0 --source-root <fixed-source> \
  --source-commit <commit> --output <new-directory> \
  --users 1 10 30 50 --concurrency 20 --delay-ms 0
python docs/reports/service-cpu-2026-10-04/verify.py
```

다음 후보는 별도로 반복 구성되는 PublicRunService 상태 snapshot 조회문의 CPU 비용이다. 이번 변경과 합산하지 않았다. 실제 Pod 자원 제한·지속 부하·multi-process/replica DB 연결 예산 검증은 별도로 필요하다. 모델 호출 최적화·Dataset Registry 실연계·Workflow CRUD·광범위 운영 보완의 보류는 유지한다. 기존066 저장 후보의 혼합 버전 비호환과 과거067 중단 원인은 이번 시험이 해결한 것이 아니다. 베이스 병합·푸시·서비스 재배포는 수행하지 않았다.

CPU clock 검증에서도 80ms sleep의 self CPU는 약0.017ms로 대기 시간이 제외됐고 offload CPU는 별도 수집됐다. `clock-check.py`와 결과 JSON을 참조한다. 독립 검산1,200개가 통과했다. 시험용 두 컨테이너·익명 볼륨과 trial DB를 정리했고 기존18개 컨테이너 및 원래 checkout/.env를 보존했다. `cleanup.json`을 참조한다.
