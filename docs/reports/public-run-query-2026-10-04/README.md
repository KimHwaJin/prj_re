# Run 상태 조회문 재사용: 서비스 CPU·완료 시간 비교

표준50명3회 평균 전체 완료 시간은 **21.529 → 16.610초(22.8% 감소)**, API CPU는 **20.761 → 15.792초(23.9% 감소)**였다. 직전069 완료 상태와 같은 한도·풀·LLM0/HTTP Executor fixture로 비교했다. 보조20 Operation의1명에서는 시간·CPU 개선이 없었으므로 모든 요청에 같은 개선율을 적용하지 않는다.

## 문제와 구현

기존 `PublicRunService._snapshot_query()`는 매번 root/latest ORM alias, 최신 invocation 하위 조회, scalar Bundle·Task join을 구성했다. DB의 SQL 준비 캐시가 켜져 있어도 Python에서 이 구조와 cache key를 새로 만드는 비용은 남는다.

`runs/public_state_query.py`에서 두 불변 SQL 선언을 한 번 만든다. 배치 snapshot은 expanding UUID 목록을, 단건 read는 Run/session UUID를 매번 새로 bind한다. 결과·권한·DB session은 캐시하지 않고 모든 호출에서 SELECT를 실행한다. 기존 `_snapshot_query`는 제거했다. 기존 SSE의 frame 공유·notify/cache 정책과는 별개다.

root/latest/Task는 여전히 한 SQL statement snapshot이다. 최신 invocation의 created_at/run_id 순서, legacy ID→public ID 해석, taskless legacy 처리, scalar-only 반환을 보존한다. `read`의 require_session과 list/SSE 호출부 권한 확인도 그대로다. 모델/승인/receipt/이벤트 순서/체크포인트 포맷·durability를 변경하지 않았다.

## 동일 조건과 범위

- 변경 전 `61c25f7bbb5d505718afbb708a770e44a6bd2902`: 069 완료 상태. 기존 claim SQL 재사용 포함, 보류된066 증가분 checkpoint 후보 제외.
- 변경 후 `0a1fe5b027a991ea63fe1b8921f70e86eaba7bb6`: 새 SQL 선언 모듈과 PublicRunService 호출 변경만 운영 소스 변경. 테스트 파일은 source audit에서 별도 구분.
- API·SSO fixture·CRUD PG·Checkpoint PG·Redis Streams·공통 Worker·HTTP Executor fixture·HITL·최종 Markdown/SSE 실행. 모델은 고정 응답·지연0, Executor는 합성 manifests/출력이며 제출한 Python은 실행하지 않는다. 출력 무결성 검사를 유지한다.
- 총한도20, CRUD pool10/overflow0, checkpoint pool4, event pool4, SSE0.5초, 취소확인0.25초, notify on. 설정·한도·API 계약·모델 호출 수는 동일.
- 표준4 Tool/2 Operation/모델4회:1·10·30명 전후 각각1회,50명 각각3회. 전후 순서를 번갈아 수행하며 동시에 벤치마크·테스트를 실행하지 않는다.
- 보조20 Tool/20 Operation/출력별64KiB/모델23회:1·10명 각각 전후1회. 큰 출력/다단계의 서비스 경로 확인이며 표준 수치와 합산하지 않는다.
- CPU 프로파일러 on의 표준10명 전후1회는 원인 확인용이며 성능 평균에서 제외한다. 모든 시도를 attempts.json에 보존한다.

## 프로파일러 없는 비교

| 시나리오 | 사용자 | 전/후 반복 | 전체 완료 전→후(초) | 시간 감소 | 사용자 평균 전→후(초) | API CPU 전→후(초) | CPU 감소 |
|---|---|---|---|---|---|---|---|
| standard | 1 | 1 | 1.768 → 1.657 | 6.3% | 1.768 → 1.656 | 0.394 → 0.338 | 14.0% |
| standard | 10 | 1 | 4.312 → 3.527 | 18.2% | 4.102 → 3.384 | 3.527 → 3.014 | 14.5% |
| standard | 30 | 1 | 13.037 → 9.845 | 24.5% | 12.325 → 9.448 | 12.427 → 9.378 | 24.5% |
| standard | 50 | 3 | 21.529 → 16.610 | 22.8% | 20.526 → 15.762 | 20.761 → 15.792 | 23.9% |
| large20 | 1 | 1 | 4.186 → 4.215 | -0.7% | 4.186 → 4.214 | 1.325 → 1.416 | -6.9% |
| large20 | 10 | 1 | 15.855 → 14.775 | 6.8% | 15.725 → 14.554 | 14.980 → 13.732 | 8.3% |

전체 완료는 측정 cohort 시작부터 마지막 terminal SSE까지다. 사용자 평균은 개별 flow의 완료 시간 평균이며 각 trial p95는 nearest-rank이다. API CPU는 reset부터 owner·CRUD pool drain 표본까지 API 프로세스의 process_time이다. PG/Redis/fixture/원래 실행 중인 컨테이너의 CPU를 합한 값이 아니다. 50명은 같은 크기의 세 trial 산술평균이며 다른 사용자 수·시나리오를 섞지 않는다. 음수 감소율은 증가를 뜻한다.

50명 완료 시간 범위: {"before": [21.47582741593942, 21.579156250227243], "after": [16.370962708024308, 17.015214332845062]}. 다른 조건은1회여서 안정 평균·통계적 유의성을 주장하지 않는다. 로컬 Mac/Docker의 유한 burst이며 실제 Pod CPU/memory 제한·지속 유입·HPA 용량을 뜻하지 않는다.

## CPU 진단과 계측 영향

표준 라이브러리 cProfile + thread_time으로 메인 스레드와 ThreadPoolExecutor 작업의 CPU를 분리한다. DB/HTTP wall wait를 CPU로 합산하지 않는다. exclusive self CPU만 그룹 합산하고 async/greenlet cumulative 값으로 전체 함수 점유율을 계산하지 않는다. instrumentation/native/미계측 스레드 residual은 미귀속이다. profiler가 켜진 실행을 속도 평균에 섞거나 overhead를 사후 차감하지 않는다.

- before-standard10-profile: process CPU 9.399초, main 9.136초, offload 0.186초; SQLAlchemy main self 4.279초. projection builder calls 271, cache-key 재귀 calls 85,758.
- after-standard10-profile: process CPU 7.282초, main 7.024초, offload 0.186초; SQLAlchemy main self 2.538초. projection builder calls 0, cache-key 재귀 calls 32,704.
- before 10명 profiler off/on 완료 4.312 → 9.762초(+126.4%). 각1쌍의 overhead 표본이며 안정 평균이 아니다.
- after 10명 profiler off/on 완료 3.527 → 7.684초(+117.9%). 각1쌍의 overhead 표본이며 안정 평균이 아니다.

SQLAlchemy self CPU 전체를 상태 조회문 구성 한 곳의 비용이라고 간주하지 않는다. SQL 조회 횟수는 바인딩 재사용으로 의도적으로 줄이지 않았다. 더 빨라지면 SSE 표본/notify 시점의 조회 횟수도 달라질 수 있으므로 SQL count 변화 자체를 필수 조회 제거의 성과로 표현하지 않는다. snapshot_sql_total_ms는 겹치는 DB 호출의 누계여서 E2E에서 뺄 수 없다.

## 검증·인계

관련 실제 PostgreSQL/API/SSE 회귀51개(skip0)가 통과했다. 고정 Run ID와 여러 resume·legacy alias·Task 완료/잠금/취소·소유권·모델 선택·한 커넥션 반환·SSE cursor/notify/동시 탭·권한 폐기 등을 확인했다. 신규 테스트는 expanding ID 목록 길이/빈 목록/중복/없는 ID의 반복 조회, 외부 commit 후 동일 reader의 최신 값, dirty ORM 객체 보존, 여러 사용자·세션·Run의 동시 바인딩 분리를 확인한다. 3개 경고는 기존 모델 역할 시험의 checkpointer 없는 graph에서 durability가 효력이 없다는 경고다. 운영 checkpoint 설정을 바꾼 것이 아니다.

원본 gzip·SHA, 소스 manifest, 결과/HTTP·모델 호출·command/owner/Inbox drain·집계를 verify.py로 재계산한다. warmup execution은 measured session으로 필터한다. 결과 원문·최종 보고서·4/20개 완료 observations를 검산한다. 결과·폴링 정책·스키마·환경변수 추가는 없다.

- `attempts.json`: 전체 시도·종료 코드·고정 소스·원본 hash.
- `measurements.json`, `comparison.json`: 개별 수치와 전후 산식.
- `profiles.json`: CPU 그룹과 조회문 구성 횟수. 상세 frame은 raw/.
- `source-audit.json`, `environment.json`: 운영 변경 범위·동일 harness hash·Python/라이브러리·Docker image 버전.
- `regression.log`, `verify.py`, `verification.json`: 검증 증거.

## 재현과 남은 범위

동일 고정 소스와 소유한 전용 localhost63372/63373의 빈 시험 자원을 사용한다. 기존 운영 DB를 지정하지 않는다.

```sh
PYTHONPATH=src .venv/bin/python scripts/benchmarks/worker_e2e/run.py \
  --database-url postgresql+asyncpg://postgres:<scratch-password>@127.0.0.1:63372/postgres \
  --redis-url redis://127.0.0.1:63373/0 --source-root <fixed-source> \
  --source-commit <commit> --output <new-directory> \
  --users 1 10 30 50 --concurrency 20 --delay-ms 0
python docs/reports/public-run-query-2026-10-04/verify.py
```

아직 실제 Pod 자원 제한/지속 부하/multi-replica DB 연결 예산을 검증하지 않았다. 모델 호출 최적화·Dataset Registry·Workflow CRUD·광범위 운영 보완 보류를 유지한다. 기존066 혼합 버전 비호환·067 최초 중단 원인은 이번 변경으로 해결됐다고 표현하지 않는다. 베이스 병합·원격 push·실제 서버 재배포는 수행하지 않았다.

전체18회·424개 사용자 흐름에서 실패0, 독립 검산1,644개 통과. 시험용2개 컨테이너·익명 볼륨/DB를 정리하고 기존18개 컨테이너·원래 checkout/.env를 보존했다. `cleanup.json`을 참조한다.
