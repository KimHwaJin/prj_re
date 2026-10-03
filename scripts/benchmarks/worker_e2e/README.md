# 공통 Worker 전체 HTTP 경로 대조

통합 직전 `faca5b1`과 현재 `32f443e`의 runtime을 같은 runner/server/model fixture로 실행한다. 실제 API/SSO cookie·CSRF·CRUD·LangGraph/create_agent/middleware·checkpoint/Store·Executor HTTP 계약·manifest/checksum·Redis Streams·Inbox·SSE를 거친다. 사내 직원 검증 verdict, 모델 전송, Executor 코드 실행/분석 결과는 fixture다. 실제 LLM/Executor/Jupyter/MinIO에는 접속하지 않는다. 이 패키지는 운영 진입점/기능과 분리한다.

## 리소스와 재현

Runner는 반드시 dedicated disposable PostgreSQL `127.0.0.1:63372/postgres`, Redis `127.0.0.1:63373/0`를 사용한다. DB의 새 `service_perf_<uuid>`·checkpoint DB만 생성·migration·reset·삭제한다. 서비스의 기존 DB, env, Redis group/Stream은 쓰지 않는다. Executor fixture와 API는 별도 localhost 임시 port에서 시작하고 종료한다. 각 trial의 config는 0600 권한이고 정상 종료 시 삭제한다. 기동 전 migration 실패는 진단 자료를 남길 수 있으므로 해당 실패 폴더의 private config에 기록된 자기 fixture 경로만 진단 후 정리한다. 새 출력 폴더를 사용한다.

```sh
<existing Python 3.11> scripts/benchmarks/worker_e2e/run.py \
  --database-url '<dedicated disposable asyncpg postgres URL on port63372>' \
  --redis-url redis://127.0.0.1:63373/0 \
  --source-root '<archived baseline or current source root>' \
  --source-commit '<verified source commit>' \
  --output '<fresh private output>' \
  --scenario executor --users 1 10 30 50 --concurrency 20 --delay-ms 5000
```

비교는 **기존 사용자16+결과4 = 총20, 현재 공통20**이다. `--concurrency`는 이 runner에서 총한도다. 이전 runner의 API 전용 한도와 혼동하지 않는다. 단일 API 프로세스·CRUD10/overflow0·checkpoint4·event4·bridge4·memory2를 동일하게 둔다. 실제 configured maxima와 순간 checkout은 다르다. `--notify off`는 현재 소스의 원장/슬롯을 유지한 polling 대조군이다. `--trial-index`는 독립 반복 식별자, `--repeat`는 전체 명령 반복이다.

- `approval`: Project/Session 생성→계획→정책 편집→HITL 대기. Executor 제출 없음.
- `executor`: 위 계획 승인→MULTI 제출/continue/finalize→관찰4개→Markdown 리포트 완료.
- `result_burst`: 사용자n명의 첫 제출을 준비하고 owner 반환을 확인한 뒤 결과를 일제 해제한다. **해제→리포트 완료**만 측정한다. 계획 준비 비용은 범위 밖이다.
- `mixed`: ceil(n/2)의 결과를 해제하면서 floor(n/2)의 새 사용자가 전체 분석을 시작한다. 총 사용자n이며100명으로 늘리지 않는다. n=1은 결과 단독과 같은 구조다. 두 cohort를 따로 표시한다.
- `--followup --memory-mode manual|auto_context`: 전체 분석 완료 후 근거 기반 설명/보고서 설명 요청2개를 같은 session에서 추가한다. manual은 API로 audience topic을 저장, auto는 첫 후속 현재 사용자 인용에서 Agent middleware가 저장한다. 다음 질문에서 참조·source/version을 검사한다. 별도 Artifact 파일 재작성/등록은 포함하지 않는다.

## 모델·측정 경계

`model_fixture.py`는 provider를 mock 전용 경로로 바꾸지 않고 `create_chat_model`의 HTTP transport만 바꾼다. 운영 Agent의 선택→Skill metadata 조회→계획 구조검증/ProjectPrompt/SessionAnalysis/ProjectMemory middleware가 실행된다. 계획 선택, 계획 작성, review, report, 후속 answer는 모두 각5초다. 정상 전체 분석은4콜/20초 모델 대기다. repair 응답도 같은 transport fixture를 사용하지만 정상 성공 scenario는 repair를 호출하지 않는다. 오류 repair 처리량을 검증했다고 주장하지 않는다.

Warm-up은 delay0, 측정은5000ms다. 결과 집중/혼합의 준비 cohort는 delay0으로 준비하고 측정 시작 시 delay5000ms로 되돌린다. 준비 시간/CPU/SQL/model records는 측정 reset 이전이라 결과 처리 시간에 합치지 않는다. incoming cohort의 계획은 측정에 포함하여5초를 적용한다. 다른 모든 fixture·검증·풀 조건은 두 버전에 동일하다.

전체 분석 wall time은 프로젝트 생성 시작→최종 SSE snapshot이다. 리포트 완료는 Agent Markdown 응답과 ready 상태이며 별도 Executor Artifact POST 등록은 포함하지 않는다. 로그인·등록·warm-up·기동/종료는 제외한다. 생각 시간0·유한 일제 유입이며 지속 유입 한계/SLA/HPA로 환산하지 않는다. p95는 사용자 wall time 선형 보간이다. 반복 p95는 별도로 남기고 평균 p95를 합친 모집단 p95로 부르지 않는다.

User queue는 측정 내 user invocation의 created→started; Event queue는 XADD→성공 handler 시작이며 transport/ingest/router/claim을 포함한다. CPU는 API 프로세스만, SQL count/time은 CRUD asyncpg 계측만이며 psycopg checkpoint/Store/Ingress/bridge SQL과 DB/Redis/mock/client CPU는 제외한다. pool acquire·SQL·model·worker 시간은 겹치므로 합쳐 E2E 비율로 만들지 않는다. 실행 자리 사용률은 slot work seconds/(makespan×총20)이다. RSS/PG activity는약0.5초 표본이다. psycopg pool 누계는 warm-up을 포함하므로 측정 SQL 총량으로 쓰지 않는다.

## 검증

`analyze.py`가 완료 cohort 전부, 고유 Run/Command/Event, interrupted private invocation과 public terminal의 구분, 동일 총한도·실제 shared peak, 모든 호출5초, 역할별 호출 수, 실제 source snapshots·관찰4개·정상 finalize, 종료 owner/recovery/queue/checkout을 확인한다. Defer는 재실행 성공과 구분해 공개한다. 실패 trial을 완료 사용자만으로 계산하지 않는다.

```sh
<existing Python> scripts/benchmarks/worker_e2e/analyze.py '<capture root>' --output '<summary.json>'
DTEST_WORKER_E2E_CAPTURE='<actual executor raw.json>' \
  <existing Python> -m pytest -q scripts/benchmarks/worker_e2e/test_capture.py
```

기존 057 executor_throughput runner는 그 당시 two-dispatcher capture 계약으로 보존한다. 이 패키지는 새 비교 기준을 제공하며 옛 결과를 새 측정으로 덮어쓰지 않는다. Kubernetes/멀티 Pod/HPA·CPU quota·실제 대용량 PVC/모델 제공자·장기 Executor와 rolling migration은 별도 검증이다.

## 전체 59회 비교와 보고서 재생성

`matrix.py`는 위 시나리오 네 종류를 두 구조에서 수행한다. 1·10·30명은 각 1회, 50명은 각 3회이고 두 번째 반복은 실행 순서를 바꾼다. 후속 요청·메모리 8회와 현재 notify OFF 3회를 합쳐 **59 trial·1,722 사용자 시나리오**다. 동시 실험을 실행하지 않는다. 완료 capture는 재검증 후 건너뛰고, 실패·불완전 capture는 덮어쓰지 않고 중단한다.

기준 소스는 검증한 commit의 `git archive`로 별도 디렉터리에 풀어 둔다. 현재 소스도 비교하려는 commit의 정본이어야 한다. 소스 commit 이름은 각 capture에 기록하고 실제 `src/**/*.py` hash를 별도로 검사한다. 기존 checkout의 변경 사항을 이동하거나 덮어쓰지 않는다.

```sh
<existing Python> scripts/benchmarks/worker_e2e/matrix.py \
  --database-url '<dedicated disposable asyncpg postgres URL on port63372>' \
  --redis-url redis://127.0.0.1:63373/0 \
  --baseline-root '<archive of faca5b1>' --baseline-commit faca5b1 \
  --current-root '<verified current source>' --current-commit 32f443e \
  --output '<fresh capture root>'

<existing Python> scripts/benchmarks/worker_e2e/export.py '<capture root>' --output '<report folder>'
<existing Python> scripts/benchmarks/worker_e2e/verify.py '<report folder>'
<existing Python> scripts/benchmarks/worker_e2e/build_report.py '<report folder>'
```

`export.py`는 모든 trial을 다시 검증한 뒤 원문 gzip/SHA와 실제 SQLite 집계 SQL을 남긴다. `verify.py`는 analyzer를 import하지 않고 원문에서 평균·p95·처리율·SQL/사용자·반복 편차·Agent Python 소스 동일성을 독립 검산한다. 50명 평균의 표준편차는 trial 평균 3개의 표본 표준편차이며 사용자 전체 분포의 표준편차가 아니다.

HTML은 canonical `artifact.json`을 Data Analytics `build-report`의 packaged `deliver_portable_artifact.mjs`에 입력해 만든다. 자체 HTML renderer는 두지 않는다. `report.html`은 읽기용 결과, `results.json`·`summary.json`·`raw/`는 검산 근거다. 당시 실행 제어 코드·환경·dependency/source audit도 보고서 폴더에 남긴다. Smoke 및 시험 준비 중 설정 충돌은 성능 모집단에 포함하지 않는다.


## Checkpoint 저장량/대기 프로파일 (063)

`--checkpoint-profile`은 실제 Saver의 get/put/intermediate-write를 메모리에서
관측하고, 최종 cohort의 owner/connection이 반환된 뒤 별도 SQL로 저장 행을
조회한다. 이 SQL 캡처는 사용자 wall time 밖이다. 실제 직렬화 결과 bytes를
읽으며 payload를 다시 직렬화하거나 저장 내용을 변경하지 않는다.

`--checkpoint-lock-profile`은 위 옵션과 함께 전 버전의 공통 lock 대기를
관측한다. 원래 lock을 유지하는 wrapper이며 병렬화를 켜는 설정이 아니다.
`analyze_checkpoint.py`의 해당 보조 시험은 단일 Saver lock 진입1을 검증한다.
후 버전의 호출별 Saver에는 그 보조 관측 판정을 적용하지 않는다.

```sh
<existing Python> scripts/benchmarks/worker_e2e/run.py \
  --database-url '<dedicated disposable asyncpg postgres URL on port63372>' \
  --redis-url redis://127.0.0.1:63373/0 \
  --source-root '<verified source root>' --source-commit '<verified commit>' \
  --output '<fresh capture root>' --scenario executor \
  --users 1 10 30 50 --concurrency 20 --delay-ms 5000 --checkpoint-profile

<existing Python> scripts/benchmarks/worker_e2e/analyze_checkpoint.py \
  '<capture root>' --output '<checkpoint results.json>'

DTEST_CHECKPOINT_CAPTURE='<actual profiled raw.json>' \
DTEST_CHECKPOINT_LOCK_CAPTURE='<pre-change lock-profile raw.json>' \
  <existing Python> -m pytest -q scripts/benchmarks/worker_e2e/test_checkpoint_profile.py
```

`export_checkpoint.py`은 `--capture label=/absolute/root`로 `e2e`·`repeat`·
`followup`·선택적 `lock`의 한 runtime 모집단을 검산/gzip/SHA로 내보낸다.
주 조건1/10/30/50명과 50명 반복1/2/3이 있어야 한다. 전후 source는 각각
별도 export한다. 한 export에 다른 runtime hash를 섞으면 실패한다.
`--repeat 2 --trial-index 2`는 raw의 절대 반복2/3을 뜻하며 폴더의 r1/r2는
해당 CLI 호출 내부 반복이다. 과거062는 repeat1/명시 trial-index였으므로
그 당시 결과 식별에 이 문제가 없었다.

Byte 집계는 고유 channel/version을 한번만 세고 최신 참조와 과거 버전을
분리한다. JSON::text/bytea의 논리 길이, 압축 후 column size, 전체 relation
size는 서로 다른 지표다. relation은 warmup을 포함한다. phase별 write는
연결 parent checkpoint에 귀속하여 실제 실행 node 시간과 구별한다.
저장 함수 외부 Trace는 adapter 생성까지 포함하고 호출 count가 내부
profile과 일치해야 한다. aget_tuple 내부 profile은 후 버전 adapter 생성
비용을 제외하며 모든 비용은 E2E에 포함된다. 함수 누계를 그대로 wall
time에서 빼거나 겹치는 DB/graph 시간을 합산하지 않는다.

[063 측정과 제한](../../../docs/reports/checkpoint-profile-2026-10-04/README.md).
