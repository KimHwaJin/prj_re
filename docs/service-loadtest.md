# LLM 없는 서비스 부하테스트

LLM 응답을 고정하고 API → PostgreSQL Run 큐 → Run Worker → LangGraph → checkpoint → HITL → Workflow 저장 → Executor 제출 경로를 실제로 실행한다. Executor는 실제 API와 별도 Mock HTTP 서버 중 선택한다.

## 실행 및 Executor ON/OFF

저장소 루트에서 실행한다. 명령은 환경만 기동하며 부하는 자동 시작하지 않는다.

```bash
# 최초 구축 / 코드 수정 후 업데이트. 실제 Executor OFF, Mock API 사용
python3 scripts/loadtest/control.py --executor mock --build

# 실제 Executor ON (.env의 EXECUTOR_BASE_URL 사용)
python3 scripts/loadtest/control.py --executor real

# 실제 Executor OFF (Mock API로 되돌리기)
python3 scripts/loadtest/control.py --executor mock

# 마지막으로 선택한 모드를 유지하면서 소스 업데이트
python3 scripts/loadtest/control.py --build
```

모드 전환 시 API 컨테이너 설정을 반영하고 Locust UI를 재시작한다. 실행 중인 Locust 테스트가 있으면 전환을 거부한다. Standalone 실행기도 먼저 종료해야 한다. 이미 접수된 실제 Executor 작업은 모드를 OFF로 바꾸어도 취소되지 않는다. 마지막 성공 모드는 `var/loadtest/executor-mode`에 저장한다.

- Locust: <http://localhost:18089>
- API / Swagger: <http://localhost:18080/docs>
- Mock Executor 상태: <http://localhost:18081/health> (`unique_submissions`, `delay_ms`)
- API 프로세스 및 Run Worker 기본 4개
- 독립 Compose 프로젝트 `dtest-agent-loadtest`, 별도 PostgreSQL·Redis·볼륨
- 기존 `18000` 환경과 외부 DB·Redis 큐는 변경하지 않음
- 별도 Executor 이벤트 Worker는 실행하지 않음

`compose.loadtest.yaml` 단독 실행은 항상 Mock 목적지를 사용한다. 실제 모드는 `compose.loadtest.real.yaml`을 함께 적용한다. 혼동을 막으려면 위 control 명령을 사용한다. Docker Compose는 보간용 `.env`를 읽지만 서비스에 전체 `.env`를 주입하지 않는다. 실제 모드에서 Executor 주소·TLS·프로필·경로·시간 제한만 선택적으로 반영하며 모델 키·외부 DB·Redis 설정은 가져오지 않는다. 코드 전송은 INLINE이다.

## Locust: 100명 시나리오

UI에서 사용자 수 **100**, 증가 속도 예시 **10명/초**, Host `http://api:8000`으로 설정하고 `Scenario`를 고른다. 시나리오 변경 전 기존 테스트를 Stop한다.

| Scenario | 수행 내용 | Executor |
|---|---|---|
| `crud` | 사용자별 프로젝트 생성 또는 소유 프로젝트의 세션 생성을 50:50 확률로 선택 | 호출 없음 |
| `crud_mixed` | 프로젝트·세션 생성/조회/수정/삭제를 목표 비율 20/50/20/10%로 반복 | 호출 없음 |
| `submit` | 새 세션 → 데이터 선택 → 분석 목적 → 후보 선택 → Workflow 승인 → Executor 접수 확인 → 즉시 새 세션 반복 | ON이면 실제 API, OFF이면 Mock API |
| `approval` | 기존 4단계 HITL, Workflow 승인 대기에서 종료 | 호출 없음 |

Locust 가상 사용자당 계정을 한 번 생성한다. `crud`는 반복 사이 0.5~1.5초를 무작위 대기하며, 세션 생성 대상은 해당 사용자가 만든 프로젝트에서 무작위 선택한다. 클라이언트는 최근 1,000개 프로젝트 ID까지만 기억하고 DB에는 모든 생성 기록을 남긴다. `submit`은 사용자 계정과 기본 프로젝트를 재사용하고, 성공 직후 별도 대기 없이 새 세션을 만든다. 실패 후에는 1초 대기한다.

`submit` 시나리오는 총 5개 Run을 만든다. 단순 POST 202가 아니라 마지막 Run의 `EXECUTOR_EVENT` interrupt와 유효한 `execution_id`를 확인해야 성공이다. 이는 Executor 접수 응답 처리·Workflow execution 저장·execution binding·checkpoint 기록까지 완료했다는 뜻이다. 실제 분석 완료, 결과 수신, 리포트 생성은 기다리지 않는다.

Headless 예 (선택한 모드로 이미 환경을 띄운 뒤 실행):

```bash
# 시나리오 1: 프로젝트/세션 생성
# UI 실행과 headless 실행을 동시에 돌리지 않는다.
docker compose -f compose.loadtest.yaml --profile load-generator run --rm --no-deps locust \
  -f /loadtest/locustfile.py --host http://api:8000 --scenario crud \
  --headless -u 100 -r 10 -t 5m --stop-timeout 120 \
  --csv /results/crud-100 --exit-code-on-error 1

# 시나리오 2: 제출 후 새 세션 반복
# 위 control 명령으로 real/mock을 먼저 선택한다.
docker compose -f compose.loadtest.yaml --profile load-generator run --rm --no-deps locust \
  -f /loadtest/locustfile.py --host http://api:8000 --scenario submit \
  --headless -u 100 -r 10 -t 5m --stop-timeout 120 \
  --csv /results/submit-100 --exit-code-on-error 1
```

`run --no-deps locust`는 실행 중인 API 설정을 바꾸지 않는다. 이 headless 컨테이너는 control의 UI 상태 검사 대상이 아니므로 모드 전환 전에 종료한다.

## Mock / 실제 Executor 차이

두 모드 모두 실제 제출 노드와 HTTP 클라이언트를 사용한다. `EXECUTOR_SUBMIT_ENABLED=true`이며, 이 값을 false로 바꾸는 것은 Mock이 아니라 **제출 생략**이므로 `submit` 시나리오가 성공하지 않는다.

Mock 서버는 production `ExecutorRequestBody` 스키마를 검증하고 202 및 `execution_id`·operation·step ID를 반환한다. 동일 idempotency key의 동일 요청은 같은 결과를 반환하고, 내용이 다르면 409로 거부한다. SQLite에 요청 해시와 응답만 보관하고 제출된 코드는 실행하지 않는다. 실제 Executor·Jupyter·분석 연산에는 부하가 들어가지 않는다. Mock 자체의 HTTP·스키마 검증·저장 비용은 측정에 포함된다. Mock은 제출 API만 제공하며 완료 이벤트를 발행하지 않는다.

실제 모드는 생성된 Notebook 코드를 그대로 실제 Executor에 보내므로 실제 스케줄링·런타임·분석 작업이 발생할 수 있다. 고정 LLM 계획은 `data_quality_check`의 profile/statistics/outlier 도구를 사용한다. `DATA_MOCK=true`로 선택된 parquet 경로와 분석 라이브러리가 **Executor 런타임에도 있어야 분석이 성공**한다. Agent 컨테이너의 `/workspace/pv/data` 파일이 외부 Executor에 자동 복사되지는 않는다. 접수 성공률과 분석 성공률은 별개이며, 이번 테스트 지표는 접수까지다. 테스트 종료나 모드 전환은 이미 제출한 작업을 취소하지 않는다.

이벤트 Worker가 없으므로 Agent의 Task는 Executor 이벤트 대기 상태로 남는다. DB·checkpoint·Workflow·Mock 제출 이력은 반복할수록 누적된다. `down`은 볼륨을 보존한다. Mock은 실제 서비스의 오류율·스케줄러·이벤트 전달 특성을 재현하지 않는다.

## 지연 및 빠른 검증

```bash
# 각 Mock LLM 호출 100ms, Mock Executor HTTP 응답 200ms
LOADTEST_MOCK_DELAY_MS=100 LOADTEST_EXECUTOR_MOCK_DELAY_MS=200 \
  python3 scripts/loadtest/control.py --executor mock

# 제한된 횟수 검증 (매 시나리오 새 사용자 생성; 100명 반복 시험은 Locust 사용)
.venv/bin/python scripts/loadtest/run.py --scenario submit \
  --scenarios 8 --concurrency 4 --output var/loadtest/submit-smoke.json
```

기본 지연은 모두 0ms. `MODEL_PROVIDER=mock`은 모델 기반 라우팅·의도·Skill 선택·Workflow 계획만 고정 응답으로 대체한다. 일반 provider는 기존 모델 생성 경로를 유지한다. Workflow 검사·컴파일·코드 생성·저장·HITL은 실제다. FAQ·리포트·조건부 실행은 Mock 지원 범위 밖이다. LLM Mock 선택과 Executor 제출 허용은 독립 설정이다.

기본 Run timeout 120초, 상태 polling 0.25초. standalone에서 `--timeout`, `--poll-seconds`로 조절한다. 지연은 polling 간격의 영향도 받는다.

## 지표

- HTTP GET/POST/PATCH/DELETE: API 응답 시간. UUID URL은 `:id`로 묶음
- `FLOW RUN/<단계>`: 큐 대기 + Worker 실행 + 다음 HITL까지의 시간
- `FLOW RUN/executor_submitted`: 승인 요청부터 코드 생성·제출·execution 저장·대기 상태 기록까지
- `FLOW SCENARIO/executor_submit`: 세션 생성부터 제출 확인까지, 사용자 준비 제외
- `FLOW SCENARIO/create_project`, `create_session`: 자원 생성 완료 시간
- `FLOW SCENARIO/approval_wait`: 기존 승인 대기까지 시간

`Aggregated`에는 HTTP와 FLOW가 함께 합산되므로 API RPS가 아니다. 실제 분석 처리량은 별도로 Executor에서 확인한다. standalone JSON에는 session/run/execution ID, 시각, 성공·실패, 성공 시나리오의 재시도 횟수를 기록한다. `0ms Mock` 결과는 실제 LLM을 포함한 응답 시간이나 전체 시스템 처리량으로 해석하지 않는다.

## 이번 변경 검증 (2026-09-28)

- 새 계약/Graph/시나리오 테스트 5개 통과: 승인부터 제출·binding·interrupt, 요청 검증, 멱등성, 접수 미확인 실패 처리 포함.
- Docker HTTP Mock 제출: 8개 시나리오 / 40개 Run 성공, 실패·재시도 0 (`var/loadtest/executor-mock-validation.json`).
- Locust 4명 짧은 검증: 프로젝트 32건 / 세션 18건 생성, 별도 제출 반복 25회 / 125개 Run, HTTP·FLOW 오류 0 (`crud-validation_*.csv`, `submit-validation_*.csv`). 100명 용량 검증은 아직 실행하지 않음.
- 실제 목적지 전환 후 Mock 복구 확인. 실제 전송은 자동 승인 검토가 외부 코드·컨텍스트 전송 승인을 요구하여 차단했으므로 실제 접수는 미검증.
- 검토용 요청 예시: `var/loadtest/executor-request-preview.json`. 6개 INLINE step (data_load 2개, profile_data, compute_statistics, detect_outliers, workflow_outputs). 실제 API 시나리오에서는 사용자/세션/Task UUID가 새로 생성됨.
- 기존 Executor toggle 테스트까지 함께 실행하면 9개 통과 / 1개 실패. 실패는 변경하지 않은 PATH 모드 테스트가 절대 경로를 기대하지만 기존 구현이 상대 경로를 반환하는 불일치다. 이번 설정은 INLINE 모드이며 해당 구현·테스트는 변경하지 않았다.

## 1~100명 CRUD 단계 시험 및 리포트

UI에서 다른 테스트가 실행 중이지 않은 상태에서 다음 명령으로 재현한다. 단계별 목표는 1, 5, 10, 25, 50, 75, 100명이며 각 단계 15초 안정화 후 60초를 측정한다. 동일한 사용자 집단을 점진적으로 늘리고, 마지막에 Locust를 중지한다. 기존 결과가 있는 출력 폴더는 덮어쓰지 않는다.

```bash
.venv/bin/python scripts/loadtest/crud_ramp.py \
  --output var/loadtest/crud-ramp-new-run
```

단계별 Locust JSON, Docker CPU·메모리 표본, DB 연결·잠금 표본, 테스트 전후 DB 행 수를 저장한다. 측정 중에는 API·DB 설정을 변경하거나 다른 부하테스트를 함께 실행하지 않는다. 실제 Executor 전환과 무관하게 `crud` 시나리오만 호출한다.

2026년 9월 28일 실행 결과는 `var/loadtest/crud-ramp-20260928/`에 있으며, 분석 원본은 `docs/reports/crud-ramp-2026-09-28/artifact.json`, HTML 리포트는 같은 폴더의 `report.html`이다. `build_crud_report.py`는 해당 기본 7단계 시험의 JSON/CSV 및 보고서 입력을 생성한다. HTML은 Data Analytics의 portable artifact builder로 패키징한다.

## 혼합 CRUD 유지 시험과 Executor 제출 전 단계 시험

`crud_mixed`는 사용자마다 새 프로젝트와 세션을 준비한다. 사용자가 이번 시험에서 만든 자원만 수정·삭제하고, 기본 프로젝트 및 보호용 프로젝트는 삭제하지 않는다. 삭제할 자원이 없을 때 생성으로 대체하므로 실제 실행 비율은 목표와 다를 수 있다. 반복 사이 0.5~1.5초를 대기한다.

아래 두 명령을 순서대로 실행하면 혼합 CRUD 100명 15분 유지 후, 승인 대기까지 1~100명 단계 시험을 실행한다. 각 출력 폴더는 새 경로여야 한다. 실행 중인 UI/headless 시험이 없어야 하며, 시험 도중 API·DB 설정은 변경하지 않는다.

```bash
# 선택 사항: 성공 여정의 Run timestamp를 남겨 큐 대기와 실행 시간을 나눠 분석한다.
# Locust가 중지된 상태에서만 재생성한다.
LOADTEST_JOURNEY_LOG=/results/approval-new-run/journeys.jsonl \
  docker compose -f compose.loadtest.yaml --profile load-generator up -d --no-deps --force-recreate locust

.venv/bin/python scripts/loadtest/crud_ramp.py --scenario crud_mixed \
  --output var/loadtest/crud-mixed-new-run --users 100 --warmup 30 --seconds 900

.venv/bin/python scripts/loadtest/crud_ramp.py --scenario approval \
  --output var/loadtest/approval-new-run --users 1,5,10,25,50,75,100 --warmup 15 --seconds 60
```

`approval`은 세션 생성 → 최초 Run → 데이터 선택/분석 목적/후보 선택을 위한 resume 3회 → `workflow_approval` interrupt 확인까지 수행한다. Workflow 승인 명령은 보내지 않으므로 Executor 제출 노드를 실행하지 않는다. 최초 호출과 resume 모두 `POST /api/v1/sessions/{session_id}/runs`이며 resume은 `command`와 `metadata.resume_run_id`를 보낸다. 상태는 `GET /api/v1/sessions/{session_id}/runs/{run_id}`로 확인한다. 완료 후 즉시 새 세션에서 반복한다.

측정 종료 후 이미 시작한 여정은 최대 180초 동안 마무리한다. 리포트의 측정 통계는 종료 대기를 제외하고, 전체 DB 증가량과 journey log는 준비·안정화·종료 대기를 포함한다. `FLOW SCENARIO/approval_wait`의 완료 여정/초와 polling을 포함한 HTTP RPS를 구분해야 한다.

2026-09-28의 순차 실행은 `scripts/loadtest/followup_suite.py`, 원시 결과는 `var/loadtest/crud-mixed-soak-20260928/`와 `var/loadtest/approval-ramp-20260928/`에 기록한다. 두 시험이 끝난 뒤 `scripts/loadtest/build_followup_report.py`로 실제 SQLite 집계·CSV·보고서 입력을 생성한다. 이 스크립트는 해당 날짜와 기본 7단계 구성을 대상으로 한다.

실행 결과: 혼합 CRUD 100명/900초, HTTP 89,305건 실패 0건. 승인 대기 시험은 7개 측정 구간에서 2,082여정·62,519 HTTP, 측정 구간 오류 0건이었다. 다만 안정화·종료 대기까지 포함한 전체는 성공 2,737여정과 **120초 타임아웃 1건**이다. 100명 구간 완료 여정의 p95는 28초이며, 측정 창 밖에서 확정된 타임아웃은 이 분포에 포함되지 않는다. 정체 Run은 진단 증거를 보존한 뒤 cancel API로 취소했다. 최종 running/pending 0, Locust stopped/사용자 0, Mock Executor 제출 수 33건으로 변화 없음.

상세 결과와 제한은 `docs/reports/service-followup-2026-09-28/report.html`에 있다. `stalled-run-diagnosis.json`은 취소 전후 Run·Task·이벤트, `final-verification.json`은 취소 후 잔여 실행 및 로그 검증이다. 부하 제어기의 `/stop` 15초 timeout은 graceful drain보다 짧아 종료 기록을 후속 수집했으며, 현재 스크립트는 해당 요청에 200초를 적용한다. 서버 정체의 근본 원인은 아직 확정되지 않았고 제품 코드는 수정하지 않았다.

## Run 지연 원인 계측 (기본 OFF)

`LOADTEST_RUN_DIAGNOSTICS_DIR`을 설정하면 로컬 부하테스트 API의 Worker 실행 경계에서만 구간 계측을 켠다. 예:

```bash
# 부하와 진행 중 Run이 모두 없는 상태에서만 API를 교체한다.
LOADTEST_RUN_DIAGNOSTICS_DIR=/app/var/diagnostics/my-investigation \
  docker compose -f compose.loadtest.yaml up -d --no-deps --wait api
```

`src/api_service/core/run_diagnostics.py`가 Run/Session ID와 PID, 구간별 횟수·합계·최대 시간을 프로세스별 JSONL에 기록한다. 연결 풀 준비/획득/반환/종료, Graph 생성, Graph 호출, chain callback, checkpoint 읽기/쓰기, 결과 영속화, 최종 commit, SQL verb별 실행 시간을 구분한다. SQL 원문·인자, Graph 입력/출력, 프레임 지역 변수는 기록하지 않는다. `RUN_DIAGNOSTICS_DIR`이 비어 있으면 callback·SQL listener·파일·감시 태스크를 생성하지 않는다.

실행이 5초를 넘으면 5초 간격으로 진행 중 span, pool 통계, 해당 프로세스의 관련 asyncio 작업 await 경로를 저장한다. 이 snapshot은 **느린 실행의 증거이며 자동으로 결함을 판정하지 않는다.** 취소·재시도·timeout 정책을 바꾸지 않는다. 이벤트 루프 지연은 100ms 간격의 감시 태스크로 관측하므로 100ms 미만의 Run에는 관측 기회가 없을 수 있다. 같은 이벤트 루프가 동기 호출로 완전히 멈추면 해제 전까지 snapshot을 남기지 못한다.

checkpoint 작업·노드·SQL은 Graph 실행과 중첩/병렬일 수 있으므로 시간을 모두 더하면 안 된다. `scripts/loadtest/analyze_diagnostics.py`는 겹치지 않는 최상위 구간과 중첩 지표를 별도로 집계한다. 실행 정체 시 snapshot의 task 목록은 같은 프로세스의 다른 Run도 포함할 수 있으므로 owner와 active span, Run ID를 함께 확인한다. 계측 자체의 비용이 추가되므로 이전 계측 없는 결과와 작은 수치 차이는 성능 변화로 단정하지 않는다.

분석 원본은 컨테이너의 진단 폴더를 복사해 보존한다. DB 및 이미지 교체 없이 두 부하 조건을 순서대로 비교할 때는 계측 설정도 동일하게 유지한다.
