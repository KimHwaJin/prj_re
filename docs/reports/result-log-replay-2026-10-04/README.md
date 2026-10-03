# 결과 로그 중복 확인 일괄 조회: 서비스 성능 비교

**로그 중복 확인 조회는 줄었지만, 표준50명 완료 시간과 API CPU는 사실상 동일했다.** 전후3회 범위가 겹치고 평균 차이도 작아 이 조건의 속도 개선으로 판단하지 않는다.

표준50명3회 평균 전체 완료는 **16.362 → 16.337초(0.2% 감소)**, API CPU는 **15.694 → 15.636초(0.4% 감소)**였다. 직전070 완료 상태와 동일 조건으로 비교했다. 음수 감소율은 증가이며, 이 로컬 표본의 개선율을 실제 LLM/Executor·운영 처리량에 적용하지 않는다.

## 무엇이 문제였고 무엇을 바꿨나

공개 이벤트를 DB에 반영할 때 AgentRunLogService.create는 이벤트마다 로그와 연결된 TaskEvent가 모두 존재하는지 SELECT했다. 이는 최초 저장값 보존·재개/재전달 시 중복 방지·과거 누락 복구를 위한 조회다. 확인 자체를 삭제하지 않고, 같은 짧은 저장 transaction의 Run별 대상 키를 묶어서 조회한다.

GraphResultBatch가 이미 획득한 Run advisory barrier 안에서 완성된 로그/이벤트 쌍을 한 번 조회한다. 512키씩 나누어 bind 개수를 제한하고 조회문 구조를 재사용한다. 모든 chunk가 성공한 뒤에만 lookup을 공개한다. 같은 DB session·transaction·Run에서 준비한 키만 사용할 수 있고 commit/rollback·다른 session/Run에서는 재사용을 거부한다. 매 projection마다 새 lookup을 읽으며 LLM 대기나 다음 호출까지 보존하지 않는다.

완성된 쌍만 건너뛴다. 신규·미완성 로그는 기존 INSERT ON CONFLICT·행 잠금/재조회·누락 TaskEvent 복구로 흐르고, 변경된 재시도 payload보다 최초 저장값을 쓴다. 같은 batch 안의 신규 중복 키도 원래 conflict 경로로 수렴한다. event 입력 순서·Task sequence 원자 증가·로그/이벤트 동시 commit·사용자 메시지 멱등성·권한/소유권/세션 잠금은 유지했다. 공개 planning 이벤트 경로에만 prefetch를 연결했고 기존 단건/legacy 경로의 조회는 유지했다.

**채택 판단: 후보 보류.** 50명 첫 trial의 중복 확인 SQL900회를 줄였지만 표준50명 평균 시간0.16%·CPU0.37% 차이에 그쳤고3회 범위가 겹친다. 보조1명은 느려졌고10명은 단일 표본이다. 추가 lookup/payload 보유의 복잡성에 비해 속도 이득이 입증되지 않아 베이스에 반영하지 않는다. 후보 코드/시험 자료는 이 브랜치에 보존하고 다음 성능 작업은 부모89f65ea의070 소스에서 분기한다.

lookup은 완성된 ORM 로그와 payload를 transaction 동안 보관한다. 512는 SQL bind 크기이며 전체 payload 메모리 상한은 아니다. 이번 시험은 RSS 측정을 추가하지 않았으므로 장기 history의 최대 메모리·Pod 용량을 검증했다고 표현하지 않는다. 결과 구조·DB schema/migration·환경변수·총한도·풀·Agent/LLM 로직·checkpoint 포맷/durability는 변경하지 않았다.

## 먼저 목적별로 구분한 SQL

아래는 이번 표준50명 첫 번째 trial의 Worker/event_graph CRUD SQLAlchemy 계측이다. checkpoint saver·bridge psycopg SQL은 포함하지 않는다. 로그 조회 이외의 조회/잠금도 전부 불필요하다고 해석하지 않는다.

| 목적 | 변경 전 횟수 | 변경 후 횟수 | 유지/변경 이유 |
|---|---:|---:|---|
| 로그·대응 이벤트 존재 확인 | 2,050 | 1,150 | 같은 Run의 대상 키를 묶어서 확인 |
| 로그 INSERT | 700 | 700 | 원문·최초 값 보존 |
| Run→Task 조회 | 1,000 | 1,000 | 이벤트가 기록될 Task 식별 |
| Task sequence 증가 | 1,150 | 1,150 | SSE 재생 순서의 원자 할당 |
| TaskEvent INSERT | 1,150 | 1,150 | 재생 가능한 이벤트 영속화 |
| advisory barrier | 2,200 | 2,200 | 다른 lifecycle/소유권 잠금도 포함; 제거하지 않음 |
| 그 외 조회·수정 | 9,265 | 9,265 | 권한·메시지·상태·명령 등 잔여 범위 |

sql-purpose.json은 모든 trial의 category·목적별 횟수와 SQL 누계 시간을 보존한다. SQL 누계 시간에는 동시 DB 요청이 겹치므로 E2E 시간에서 차감하거나 직렬 소요 시간으로 합산하지 않는다. SSE/취소 감시는 실행 길이·알림 시점에 따라 횟수가 달라질 수 있다. 전체 SQL count 감소를 전부 이번 묶음 조회의 직접 효과로 간주하지 않는다.

동일한3개 공개 이벤트의 실제 PG 독립 probe: 최초 저장 **16→14 SQL**, replay **4→2 SQL**, commit은 모두1회다. 로그 ID·첫 payload·sequence·중복 복구 결과는 동일하다. 이것은 작은 입력의 query budget이며 E2E 개선율과는 별개다.

## 같은 조건의 성능 비교

- 전 `89f65ea53ecf2b92a19f38aa0b9f9592e0e44eee`: 직전070 완료 상태. 후 `9673b4e7ca5e68e150cf39dcc9a93ad9c4552721`: 결과 저장3개 운영 파일 변경. 이전066 저장 후보는 포함하지 않는다.
- 단일 API 프로세스, 총한도20·CRUD pool10/overflow0·checkpoint4·event4·SSE0.5초·취소0.25초·notify on. 양쪽 설정과 benchmark harness가 동일하다.
- LLM은 고정 응답·지연0이다. 실제 PG CRUD/checkpoint·Redis Streams·SSO fixture·Worker·HITL·HTTP Executor fixture·manifest/결과·Markdown/SSE까지 실행한다. 실제 Executor 계산 및 제출한 Python 실행은 하지 않는다.
- 표준4 Tool/2 Operation/모델4회:1·10·30명 각 전후1회,50명 각3회. 보조20 Tool/20 Operation/출력64KiB/모델23회:1·10명 각 전후1회. 종류·사용자 수를 섞어서 평균하지 않는다.
- 전후 순서를 번갈아 순차 수행했다. CPU profiler on의10명 전후1회는 진단용으로 분리하고 속도 평균에서 제외했다. 계측 설정은 양쪽 동일하지만 기존18개 로컬 컨테이너·Mac/Docker 자원 변동은 있다.

| 시나리오 | 사용자 | 전/후 반복 | 전체 완료 전→후(초) | 시간 감소 | 사용자 평균 전→후(초) | API CPU 전→후(초) | CPU 감소 |
|---|---:|---:|---|---:|---|---|---:|
| standard | 1 | 1 | 1.677 → 1.722 | -2.7% | 1.677 → 1.721 | 0.363 → 0.357 | 1.5% |
| standard | 10 | 1 | 4.022 → 3.560 | 11.5% | 3.864 → 3.360 | 2.980 → 3.021 | -1.4% |
| standard | 30 | 1 | 10.299 → 9.930 | 3.6% | 9.840 → 9.370 | 9.680 → 9.381 | 3.1% |
| standard | 50 | 3 | 16.362 → 16.337 | 0.2% | 15.544 → 15.472 | 15.694 → 15.636 | 0.4% |
| large20 | 1 | 1 | 3.691 → 4.173 | -13.0% | 3.691 → 4.172 | 1.372 → 1.385 | -1.0% |
| large20 | 10 | 1 | 15.058 → 14.564 | 3.3% | 15.015 → 14.533 | 13.770 → 13.334 | 3.2% |

전체 완료는 cohort 시작부터 마지막 terminal SSE까지다. 사용자 평균은 개별 flow 완료 시간 평균이고 p95는 trial별 nearest-rank다. API CPU는 reset부터 owner/pool drain까지 API 프로세스 process_time이며 PG/Redis/fixture CPU는 아니다.50명은 동일 크기3회 산술평균이다. 다른 조건은1회라 안정 평균이나 유의성 검증을 주장하지 않는다.

50명 완료 범위: {"before": [16.136723417090252, 16.697795124957338], "after": [16.034346333006397, 16.804716707905754]}. 로컬 유한 burst이며 지속 유입·실제 Pod 자원 제한·HPA·멀티 replica DB 예산은 미검증이다. 직전070 보고서의 예전 측정값을 이번 before 값으로 혼용하지 않았다.

## CPU 진단과 기능 검증

cProfile/time.thread_time으로 main/offload CPU를 따로 확인했다. exclusive self CPU만 분류하고 async/greenlet cumulative를 중복 합산하지 않는다. profiler on 표본은 계측 비용이 있어 속도 평균에 넣거나 overhead를 사후 차감하지 않는다. SQLAlchemy 전체 CPU를 한 조회 함수에 귀속하지 않는다.

- before-standard10-profile: API process 7.260초, main 7.010초, offload 0.179초, SQLAlchemy main self 2.609초, cache-key 재귀 32,607회.
- after-standard10-profile: API process 7.439초, main 7.176초, offload 0.189초, SQLAlchemy main self 2.640초, cache-key 재귀 28,940회.

관련 회귀56개(skip0)가 통과했다. 신규8개는 최초/재시도 SQL budget·첫 payload·미완성 쌍 복구·한 batch의 중복·commit/rollback/타 DB session/Run 경계·512키 chunk·미준비 키 fallback·6개 동시 writer와 다른 owner의 같은 event key 분리를 검증한다. 기존 공개 이벤트 rollback·invocation delta/replay·Task 없는 단건 로그·로그/이벤트 원자성·메시지/세션 잠금 순서·SSE/API/승인·프로세스 재개를 함께 확인했다.

초기 회귀는36통과·2개의 fixture 준비 오류였다. 시험용 설정 파일에 존재하지 않는 WORKFLOW_STORAGE_PATH를 넣어 Alembic 준비 단계가 거부한 것이며, 그 시험 키를 제거한 뒤 최종56개가 통과했다. initial 로그를 보존했다. 동일 입력 portable probe는 전후각1개 통과했고 운영 코드에서 설정 오류를 우회하지 않았다.

전체 18회·424 사용자 흐름·실패0, 독립 검산1,692개 통과. source/harness/raw SHA,5개 HTTP 상태·3개 invocation·모델 호출 수·Executor 합성 출력·report 근거·event/command/owner/pool drain을 재검산했다. warmup Executor 실행은 measured session으로 필터한다.

## 재현·산출물·남은 작업

전체 시도는 attempts.json, 개별/평균은 measurements.json·comparison.json, 목적별 SQL은 sql-purpose.json, 진단은 profiles.json·raw/, 환경/소스는 environment.json·source-audit.json, 기능은 regression.log·projection-probe.json에 있다. verify.py는 원본 gzip과 고정 Git 소스를 다시 읽어 집계를 독립 검산한다. 원본 압축을 풀면 raw SHA를 검증할 수 있다.

```sh
PYTHONPATH=src .venv/bin/python scripts/benchmarks/worker_e2e/run.py \
  --database-url postgresql+asyncpg://postgres:<scratch-password>@127.0.0.1:63372/postgres \
  --redis-url redis://127.0.0.1:63373/0 --source-root <fixed-source> \
  --source-commit <commit> --output <new-directory> \
  --users 1 10 30 50 --concurrency 20 --delay-ms 0
python docs/reports/result-log-replay-2026-10-04/verify.py
```

scratch PG63372/Redis63373만 사용하는 guarded runner이며 existing DB를 지정하지 않는다. 반복50명은 trial-index와 새 output directory를 지정해3번 실행한다. 보조 출력은 observation-profile large20, 진단은 cpu-profile을 명시한다.

다음 성능 작업은 부모89f65ea(070)의 운영 소스에서 분기한다. 후보 runtime은 자동 승계하지 않고 필요한071 검증 기록만 가져간다. 다음 성능 범위는 남은 실제 INSERT/sequence 할당/Task 식별·메시지/권한 쿼리의 비용과 이행 가능한 묶음 처리의 이득/위험 대조다. 조회 횟수만 목표로 필수 보호를 삭제하지 않는다. 아직 Pod/지속 유입·장기 history RSS 용량은 미검증이다. 모델 호출 수·Dataset Registry·Workflow CRUD·광범위 운영 기능 및066 채택/버전 호환·067 최초 중단 원인 보류를 유지한다. 이번 변경이 과거 미확정 중단을 해결했다고 표현하지 않는다.

feature/result-log-replay-batch에서 구현·시험했다. 베이스 병합·원격 push·실제 서비스 재배포는 하지 않았다. 시험용 컨테이너·볼륨 정리와 기존18개 서비스 보존은 cleanup.json을 참조한다.
