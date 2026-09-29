# 비교 측정 설계와 재현 안내

- 비교: 64ad96f053f538bae798257743945752cb148f38 → 4bb5c2fc3dcb4475525ff288eab8b0b887b37525. 전체 리팩터링의 최초 코드 대비가 아니라 016 DB 수명 변경의 증분 비교다.
- 동일 Python 3.11 가상환경, macOS 로컬 단일 Uvicorn 프로세스, API+Run Worker, 실제 PostgreSQL 17 컨테이너. Docker 엔진 14 CPU / 약 15.6GiB 메모리. CPU/메모리 전용 예약은 없으므로 작은 차이는 호스트 잡음과 구분하지 않는다.
- 각 commit을 git archive로 임시 디렉터리에 내보내 그 src만 import한다. 애플리케이션 소스는 수정하지 않는다. 실행기는 계측 hook, loopback 전용 측정 endpoint, 외부 서비스를 사용하지 않는 자원 조립만 추가한다.
- 등록된 독립 사용자 1·10·30·50·100명. 한 사용자당 한 HTTP 연결 풀(최대 2개), 백그라운드 CRUD와 관리 요청은 별도 풀. HTTP는 실제 loopback TCP를 사용한다.
- 각 조건은 신규 프로세스 및 초기화된 전용 identity_test DB로 2회씩 실행한다. 순서는 before→after, after→before. API pool을 미리 열고 사용자/기본 프로젝트를 만들며 GET도 워밍한다. 워밍·사용자 등록은 측정에서 제외한다.
- 기본 실행 자리 4개, 서비스 pool_size=5, max_overflow=0, pool_timeout=2초. DB cancel 감시 간격 0.25초, monitor timeout=3초. 비교용 설정이며 운영 기본값을 변경한 것이 아니다.
- 별도 PostgreSQL checkpoint pool(min=1/max=4) 사용. 분석 그래프/CRUD/Run queue/체크포인트/HITL은 실제 코드다. LLM만 기존 ScriptedAgent 사용, Executor 미제출, Redis 소비자 비활성화, WorkflowStore는 Null(별도 카탈로그 DB 저장 제외), 산출물 비활성화.
- 초기 혼합 부하: 실제 분석 초기 Run → data_selection interrupt. LLM 호출 2회/Run. LLM 1회 지연 1초. 동시에 GET 프로젝트·POST 세션을 50:50으로 초당 20건 offered rate로 제출한다.
- 전체 HITL: 초기 요청 → data_selection → mock 선택 → analysis_context → objective 입력 → workflow_candidate_selection → candidate 선택 → workflow_approval. 4 Run/세션, Mock LLM 지연 0.1초/회. 승인/Executor API 호출은 하지 않는다.
- 폴링 간격 0.5초, HITL 응답 전 대기 0.2초. 사용자 100명은 100개 동시 그래프를 뜻하지 않으며 실행 자리는 4개다. 단계별 독립 burst이며 장시간 steady-state soak 또는 실제 사용자 행동 분포는 아니다.
- CRUD 단독: 각 사용자당 프로젝트 생성→세션 생성→세션 조회→이름 수정→세션 삭제→프로젝트 삭제를 3회 수행한다(18 요청/사용자), 회차 사이 0.1초. 기본 프로젝트는 삭제하지 않는다.
- 민감도: 초기 요청에서 LLM 0.1초·5초, 연결 4/5/10개, 실행 자리 1/4개를 선택적으로 비교한다. 전체 조합의 전수 실험은 아니다.

## 지표 정의

- 응답시간: 클라이언트 요청 시작부터 응답 수신까지. 서버 시간은 측정용 middleware 진입→응답 생성으로 별도 기록한다. 두 시간의 요청별 차이는 클라이언트/전송/서버 진입 이전 비용이며 모두 DB 시간으로 취급하지 않는다.
- p95/p99: 두 반복의 성공 요청을 합친 nearest-rank 분위수. 실패 수·상태코드·완료되지 못한 시나리오는 별도 표기한다. 실패를 성공 지연에 섞어 빠르게 보이게 하지 않는다.
- 큐 시간: agent_runs.created_at→started_at. 실행 시간: started_at→completed_at 또는 interrupted의 updated_at(대기 상태 저장 시점). 실제 LLM 시간은 run_id로 연결한 ScriptedAgent 호출 계측값이다.
- 기타 실행 시간: DB에 기록한 실행시간−실제 Mock LLM 호출시간 합. SQL만이 아니라 그래프/체크포인트/저장/감시 정리 등의 전체 잔여시간이다. SQL 시간으로 명명하지 않는다.
- 관찰/HTTP 시간: 클라이언트 단계 시간−큐 시간−실행 시간. DB admission 시각과 클라이언트 시작 시각 차이, 폴링 발견 지연, HTTP 처리 등을 포함한다. 분위수끼리 빼지 않고 개별 Run을 연결해 계산한다.
- DB 연결 점유량: 서비스 풀의 checkout→checkin 기간을 전부 합산한 connection-seconds. 4개 연결을 1초 빌리면 4 connection-seconds다. DB CPU 사용량/물리 연결 수/SQL 시간과 다르다. checkpoint pool은 이 값에서 제외한다.
- Worker 점유/Run: kind=worker의 connection-seconds / DB Run 행 수. heartbeat·취소 감시·큐 조회도 포함하며 graph 전용 수치로 오해하지 않는다.
- 풀 획득 시간: SQLAlchemy _do_get 경로의 경과시간. 새 연결 생성이 있으면 포함하므로 순수 대기시간의 엄밀한 등가는 아니다. 측정 전 풀 워밍을 수행한다.
- SQL 시간: 서비스 엔진의 before/after_cursor_execute 경과시간. Python scheduling과 DB 왕복을 포함하며 PostgreSQL CPU 시간이 아니다.
- 평균 완료시간은 성공 시나리오 기준이며 timeout이 있는 조건에서는 완료율과 함께만 해석한다. 반복당 elapsed는 모든 사용자 job 종료 시점까지이며, 마지막 백그라운드 요청 정리 시간(tail)은 별도 기록한다.

## 계측 보정 이력

예비 실행의 공유 HTTPX 연결 풀은 100명 CRUD에서 클라이언트 p95 2~3초, 서버 p95 약 0.5초의 불일치를 만들었다. 최종 실행기는 사용자별 풀과 요청별 서버 시간 헤더로 보정했다. 별도 보정 실행에서 client/server p95가 약 492/490ms 및 543/540ms로 일치했고, 평균 차이는 1.7~1.8ms였다. 예비·보정 실행은 최종 효과 집계에서 제외한다.

## 재현

`scripts/benchmarks/compare_db_scope.py --matrix main --repeats 2 --output <scratch>` 및 `--matrix sensitivity`를 같은 Python 환경에서 순차 실행한다. 환경변수 DTEST_BENCH_DATABASE_URL은 반드시 로컬 전용 identity_test DB를 가리켜야 한다. 실행기는 이 DB의 테이블을 truncate한다. 두 runner를 같은 DB에서 병렬 실행하지 않는다.

집계는 `scripts/benchmarks/analyze_db_scope.py <main-scratch> <sensitivity-scratch> --output <report-data>`다. 원본 요청·서비스 풀·SQL·그래프·Run 상태는 `raw-trials.jsonl.gz`, 조건별 수치는 `summary.csv/json`, 반복별 수치는 `trials.json`, 단계별 분해는 `stage-breakdown.json`으로 보존한다. 특정 운영 DB나 사용자 데이터를 사용하지 않는다.

## 보고서 구성·QA 계획

HTML 보고서(technical audience)는 요약→지표/조건→연결 점유·API 응답·전체 HITL→코드 원인→민감도/실패→남은 병목→다음 조치 순서로 구성한다. 수치 정의를 차트보다 먼저 배치한다. 주요 비교는 서로 다른 사용자 수/버전의 범주 비교이므로 묶은 막대 차트를 사용하며, 모든 축 단위·버전 범례를 표시한다. 세부 조건과 실패 수는 표로 제공한다.

최종 집계에서 사용자 수·Run/단계 수·소유권 반환·음수 시간·동시성 상한을 검증한다. 실패 실험은 정상 결과와 합쳐 평균 개선율을 내지 않는다. HTML은 Data Analytics canonical artifact와 portable builder로 생성하고 제공된 검증 결과를 기록한다. 실제 Kubernetes 용량/운영 SLO/장기 soak 통과로 확대 해석하지 않는다.

## 시각 경계 QA

단계별 분해는 DB와 앱의 서로 다른 시간 경계를 연결한 근사치다. agent_runs.updated_at/created_at은 DB func.now()의 transaction 시작 시각이고 started_at 등은 앱 wall clock을 사용한다. LLM/HTTP/풀 측정은 monotonic timer다. 독립 검증에서 3개 단계의 잔여시간이 -6.79ms, -2.81ms, -35.43ms였다. 이를 0으로 잘라내거나 제외하지 않았으며 independent-validation.json에 Run ID와 함께 기록했다. 수십 ms 세부 비용의 정확한 profiler로 쓰지 않는다. 연결 점유·HTTP p95의 직접 타이머 지표와 초 단위 큐 지연 결론에는 이 잔여시간을 사용하지 않는다.
