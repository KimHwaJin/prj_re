# Executor 연계까지 포함한 서비스 처리량 측정

LLM provider와 코드 실행만 fixture로 바꾸고, 현재 cookie/CSRF 인증, Project/Session CRUD,
Run 접수·계획 편집·승인, checkpoint, 실제 HTTP Executor 요청, binding/Inbox/Outbox,
Redis Streams 소비, Event Worker 재개, 관찰/Markdown 저장, SSE 완료를 실행한다.
production entrypoint와 API에는 mock 선택 옵션을 추가하지 않는다.

## 실행 조건

공유 venv의 Python과 로컬 PostgreSQL/Redis가 필요하다. `--settings-file`은 비공개 flat JSON으로
`DATABASE_URL` 또는 `database_url`이 localhost의 `agentic_runtime_test` 연결이어야 한다.
기존 DB는 접속 정보만 참조하고 migration/reset하지 않는다. 매 실행은 새 `service_perf_<uuid>`
DB와 checkpoint DB를 만들고 각 trial에서 그 DB만 초기화한 뒤 종료 시 삭제한다.
출력 폴더는 새 경로여야 하며 config/로그/raw에는 실제 사용자 정보와 인증 쿠키를 기록하지 않는다.
private config는 mode0600으로 생성하고 종료 시 삭제한다. 로그/raw도0600이다.

```sh
.venv/bin/python scripts/benchmarks/executor_throughput/run.py \
  --settings-file /private/path/local-test-settings.json \
  --output /private/tmp/executor-throughput-new \
  --users 1 10 30 50 --concurrency 16 32 --delay-ms 5000
```

유저마다 Project/Session을 새로 생성하고 1개 public Run을 계획→편집→승인한다.
private Agent invocation은3개, Executor MULTI Operation은2개, HTTP 제출/continue/finalize는3개,
의미 있는 결과 재개는 operation 완료2개 + execution 완료1개다. Event Worker가 직접 그래프를
재개하므로 Agent Run concurrency와 별도의 한도다. 기본 ingress/dispatch4, DB풀4,
Router/Outbox poll0.2초·idle 상한2초를 고정한다.
`--event-concurrency`(dispatch만; ingress4 고정), `--event-pool`, `--event-poll`, `--event-idle`로 별도 비교할 수 있다.

LLM 지연은 최초 계획 fixture의 `--delay-ms 0|5000`만 적용한다. review/report는 고정 응답으로
지연0이며 실제 `create_agent`/provider HTTP/메모리 문맥 읽기·추출 비용을 재현하지 않는다.
`--executor-delay-ms`는 각 Operation 결과 생성 전 mock 대기이고 기본0이다.
mock은 코드 AST에서 관찰 step literal만 읽고, **코드를 import/eval/exec하지 않는다**.
등록 함수의 source snapshot, 작은 합성 stdout, checksum manifest를 작성하고 실제 Redis XADD로
연속 순번의 이벤트를 발행한다. 결과에는 SYNTHETIC/no Tool code executed 표시가 있다.
actual Tool/Jupyter/PVC 대용량 분석 처리량과 구별한다.

## 기능·대기·실제 Executor 검증

`--hold-seconds 5 --users 10 --concurrency 16 --delay-ms 0`은 준비 실행 뒤 측정 유저 전원이
waiting_executor가 된 다음5초간 첫 결과를 보류한다. 그 구간의 Agent/Event 슬롯,
CRUD checkout, checkpoint/Store/Event/bridge pool available을0.25초 간격으로 저장한다.
DB가 pool에 연결된 상태와 연결을 checkout해 점유한 상태는 다르다. SSE lease/query가
간헐적으로 동작할 수 있으므로 실제 표본을 보고 판단하며 장기1주 검증으로 표현하지 않는다.

`--real-executor --users 1 --concurrency 16 --delay-ms 0`은 localhost 실제 Executor를 한 번
실행한다. private settings의 `EXECUTOR_BASE_URL`, `EXECUTOR_SHARED_RESULT_ROOT`와
`/workspace/pv/default_data/df_nce_long_format.parquet`가 준비되어야 한다.
코드 실행/실제 결과는 이 기능 대조에만 포함한다. 자체 group으로 `executor.events`를 읽고
group만 종료 시 삭제하며 기존 Stream/다른 group은 건드리지 않는다. 자신이 만든 execution
결과는 Executor 저장소에 보존한다. mock 시험은 완전히 전용 Stream/파일 경로를 지운다.

## 측정 의미와 검증

전체 시간: Project 생성 시작→리포트 success의 SSE snapshot 수신. 로그인·warmup 제외,
생각 시간0·유한 일제 유입·최대50명. `users/전체 종료 시간`은 유한 batch 처리율이다.
API CPU만 측정하며 DB/Redis/mock/client CPU는 제외한다. RSS/DB sample은0.5초 간격이다.
CRUD SQL은 asyncpg 계측이며 psycopg checkpoint/Event/bridge SQL을 포함하지 않는다.
psycopg pool 사용량 누계는 startup/warmup도 포함하므로 측정기간 총 SQL로 해석하지 않는다.

`analyze.py`는 HTTP 실패, 모델/Worker/고유 Run 수, Event 재개와 Command 매칭, 실제 결과4개,
report ready, 정상 finalize, 남은 owner/recovery/queue/checkout을 확인한다. event wait는
원본 XADD 완료→Event handler 시작이다. transport/ingest, ingest→handler, handler 내부,
handler 종료 시각→SSE 수신 차이를 별도로 계산한다. SSE는 terminal commit 뒤 owner 정리보다 먼저 도착할 수 있어 이 차이는 음수가 될 수 있고 순수 전송시간이 아니다. 세부 publish 단계는 XADD 직후의 DB outbox 상태
갱신 시작을 경계로 하므로 네트워크 전송만의 정확한 분해가 아니다. 중첩 worker/CPU/SQL 시간과
다른 사용자 이벤트 시간을 더해 전체 시간 비율을 만들지 않는다. p95는 선형 보간 percentile이다.

```sh
.venv/bin/python scripts/benchmarks/executor_throughput/analyze.py \
  /private/tmp/executor-throughput-new --output /private/tmp/executor-summary.json
DTEST_EXECUTOR_THROUGHPUT_CAPTURE=/private/tmp/executor-throughput-new/TRIAL/raw.json \
  .venv/bin/python -m pytest scripts/benchmarks/executor_throughput -q
```

서비스 성능과 외부 모델·운영 Pod 제약·초장기 실행·멀티 Pod handoff를 각각 검증한다.
출발 commit, production SHA 목록, 원문 압축/hash 및 독립 검산은 보고서에 저장한다.


`--hold-owners`는 짧은 추가 보류 진단에서 CRUD checkout의 category/보유ms를 기록한다.
주 시간 비교 matrix에는 사용하지 않는다. checkout counter에는 실제 owner 등록 이전의
pool 획득/pre-ping이 섞일 수 있어 owner 목록과 PostgreSQL active/idle transaction을 함께 본다.
0초·39회 유예 대조군처럼 모두 완료했더라도 Defer가 있으면 기본 analyze는 거절한다.
Python에서 명시한 `allow_transient_deferrals=True`만 유예 대조군을 별도 라벨로 검증하며
정상 비용 비교에 섞지 않는다. completion_to_sse도 중첩 가능성을 유지한다.
[057 측정·제약](../../../docs/reports/executor-throughput-2026-10-03/README.md)을 따른다.
