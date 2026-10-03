# 공통 Agent 명령 Worker 검증 — 2026-10-03

[060 기록](../../improvements/060-unified-agent-command-worker.md), [구조·이행 안내](../../agent-command-worker.md). 브랜치 `feature/unified-agent-command-worker`, 기준 `faca5b1`, 구현 `67c8dd0325086b6cf433f617e5517231090dab3d` (로컬 commit).

## 최종 결과와 수정 이력

| 검증 | 결과 | 증거 |
|---|---|---|
| API·Agent 기본 회귀 | 608 passed / 358 skipped / 74 warnings / 24.72초 | [unit-regression.txt](unit-regression.txt) |
| 실제 PostgreSQL 넓은 회귀, 진단 사유 수정 전 | 204 passed / 1 failed / 3 warnings / 259.24초 | [postgres-first-pass.txt](postgres-first-pass.txt) |
| 진단 스키마 수정 후 원장·취소/종료 보호·Task 진단 실제 DB 재검증 | 63 passed / 74.07초 | [postgres-boundaries-final.txt](postgres-boundaries-final.txt) |
| 마지막 이행 예산 보존까지 반영한 원장·Redis/checkpoint 연계 | 16 passed / 24.12초 | [command-integration-final.txt](command-integration-final.txt) |
| 로컬·Gaia app Run/SSE/OpenAPI·진단 enum·bootstrap·문법/import | 통과, 네트워크 없음 | [bootstrap-smoke.txt](bootstrap-smoke.txt) |

첫 DB 회귀의 실패는 `unfinished_command`를 lifecycle 조건에 추가했지만 Task 진단 응답 Literal에 추가하지 않아 진단 GET이 Pydantic 오류를 낸 것이다. `task_schema.py`와 명세에 사유를 추가한 뒤 해당 실패 사례를 포함한 관련63개가 통과했다. 첫 실패를 숨기거나204개를 전체 통과로 표시하지 않는다.

358 skip은 실제 DB·모델 등 별도 조건이 없는 테스트를 포함한다. 후속 실행들은 중복 검증을 포함하므로 숫자를 고유 테스트 총수로 합산하지 않는다. 마지막16개는 앞선 명령 검사에 포함된 중복 재확인이다. 테스트 소요 시간은 서비스 응답 시간/처리량 지표가 아니다.

## 확인한 내용

- 사용자 Task/invocation/queued 이벤트/command의 atomic commit과 원장 쓰기 실패의 전체 롤백, 같은 멱등 요청은 명령1개.
- Inbox routing 중 잘못된 binding은 sequence·source command·공통 command 전체 롤백. 중복 event 재전달은 추가 명령/결과를 만들지 않음.
- sequence2가 먼저 도착하면1을 기다린 후1/2 순서로 기록.
- 동시8개 claim 호출에서 같은 명령 소유자는1개. heartbeat를7일 전으로 바꿔도 자동 탈취 없음. 실제 여러 Pod 네트워크/HPA 시험은 아님.
- 미래 재예약인 선행 명령을 같은 세션의 후행 명령이 추월하지 않음. 다른 세션은 계속 실행.
- 공통 한도2에서 사용자1 + 이벤트1이 함께 실행되고, 세 번째 이벤트는 빈 자리가 반환된 뒤 실행. 종류별 별도 자리 없음.
- 원본 Redis event는 Inbox commit 뒤 ACK. 내부 Redis command stream을 만들거나 새 Outbox 행을 만들지 않음.
- 실제 Redis → Inbox/routing → 원장 → 공통 Worker → PostgreSQL checkpointer의 interrupt resume → 공개 Run success. WAITING_EXECUTOR에서는 명령DONE·실행 자리 반환. duplicate 결과도 공개 Run ID/결과 유지.
- 취소에서 graph 종료를 관찰하고 owner/RECOVERY 보호 유지. owner token이 바뀌면 완료 기록 거절. claim 직후 중단도 새 Worker가 자동 실행하지 않음.
- 기존 pending Run/event의 ID·event payload·업무 실패 횟수·오류 보존과 멱등 backfill. 기존 소유권·부분 이행 순서가 불확실하면 backfill 거절.
- 기존 사용자/HITL/Executor decision·repair, 모델 pin, checkpoint receipt, 세션 CRUD 보호, 취소 관측·graceful shutdown을 회귀로 확인.

원장16개 중 혼합 한도 검사와 실제 Redis/checkpoint E2E는 fixture graph/모델·Executor 결과를 사용한다. 실제 LLM API, Executor 코드 실행/Jupyter/PVC 대용량 처리를 실행한 것으로 해석하지 않는다. 같은 외부 이벤트 규격을 실제 Streams에 넣어 전달/저장/재개 경계를 확인한다.

## 격리 조건과 재현

전용 `postgres:17`(63364)·`redis:7-alpine`(63365) 컨테이너를 사용했다. 실제 .env·서비스 DB·기존 compose 컨테이너·Redis group은 사용/변경하지 않았다. PostgreSQL DB는 identity_test, agentic_runtime_test, agentic_checkpoint_test이며 fixture는 전용 schema를 재생성하고 truncate한다. 기존 Python3.11 venv/lock을 사용했고 신규 의존성을 설치하지 않았다.

```sh
PYTHONPATH=src .venv/bin/python -m pytest -q \
  src/api_service/test src/agent_service/agents/analysis/tests --disable-warnings
```

실제 DB 검증에는 guard를 통과하는 localhost **삭제해도 되는 전용 DB**를 준비한다.

- `DTEST_IDENTITY_TEST_DATABASE_URL`: `postgresql+asyncpg://<test-user>:<test-password>@127.0.0.1:<test-port>/identity_test`
- `DTEST_AGENTIC_TEST_SETTINGS_FILE`: 전용 agentic_runtime_test의 database_url과 agentic_checkpoint_test의 checkpoint_db_uri를 담은 비공개 JSON.
- `DTEST_COMMAND_TEST_REDIS_URL`: 전용 로컬 Redis URL. UUID stream/group만 만들어 검사 후 자기 stream을 삭제한다.

```sh
PYTHONPATH=src .venv/bin/python -m pytest -q \
  src/api_service/test/test_agent_commands_postgres.py \
  src/api_service/test/test_run_cleanup_postgres.py \
  src/api_service/test/test_task_diagnostics_postgres.py --disable-warnings
```

넓은 DB 회귀에는 위 원장 검사와 run_boundaries, public_run, graph_runtime, run_concurrency, initial_request_recovery, user_resume_recovery, model_selection, planning_api, agentic_execution_api, agentic_repair_api, session_execution, graceful_shutdown, crud_guards, short_transactions의 `_postgres.py` 모듈을 함께 실행했다. 최종 검증 후 검증용 컨테이너만 제거하며 [정리 증거](cleanup.json)를 남긴다.

## 아직 측정하지 않은 것

Worker 전용 NOTIFY/reconnect/fan-out, 여러 프로세스 경합 비용, 같은 총한도에서1/10/30/50명·결과 폭주/혼합의 처리량·SQL·CPU/RSS 비교는 다음4·5단계다. 공통화만으로 처리량 개선률을 제시하지 않는다. baseline의 API32+Event4와 현재32는 같은 총용량이 아니다.


## 사용자 승인 후 구 실행 코드 삭제

2026-10-03 사용자가 불필요한 코드 삭제를 명시 승인하여 같은 파생 브랜치에서 정리했다. 초기 자동 승인 검토의 삭제 거절 이후 수행한 별도 후속 작업이다. 삭제 구현 commit은 `544b791de4d0185f3ce1c095bcf1dd09c5c3183c`이며 베이스 미병합·원격 미게시 상태다.

| 검증 | 결과 | 증거 |
|---|---|---|
| API·Agent 기본 회귀 | 612 passed / 357 skipped / 74 warnings / 25.20초 | [cleanup-unit-regression.txt](cleanup-unit-regression.txt) |
| 실제 PostgreSQL·Redis 원장/소유권/종료 회귀 | 36 passed / 41.25초 | [cleanup-postgres-regression.txt](cleanup-postgres-regression.txt) |
| 미사용 Outbox mock 제거 후 종료 회귀 재확인 | 13 passed / 3.12초 | [cleanup-shutdown-regression.txt](cleanup-shutdown-regression.txt) |
| 로컬·Gaia Run/SSE/OpenAPI, 공통 Worker 조립, baseline hook·삭제 확인 | 통과, lifespan/네트워크 시작 없음 | [cleanup-bootstrap-smoke.txt](cleanup-bootstrap-smoke.txt) |

Redis Dispatcher/SessionGuard/Outbox, 테스트만 사용하던 독립 graph builder, 해당 전용 테스트 파일2개를 삭제했다. Store의 구 발행·재시도·skip 메서드7개도 제거했다. Redis lease-loss 전용 사례는 제거했고, 현재 DB token 변경·느린 취소·checkpoint 재개·강제 중단 보호 검사는 유지하여 실제 DB에서 다시 통과했다. 현재 수신 Worker 종료 테스트의 미사용 Outbox mock도 제거했다.

기본 검사 수는 이전608에서 구 전용5개를 제거하고 설정9개를 추가하여612다. 조건부 skip은 구 Redis SessionGuard 사례1개 제거로358에서357이다. 위 실행은 앞선060 검증과 중복하므로 고유 검사 총수나 처리량 수치로 합산하지 않는다.

`EW_COMMAND_STREAM_NAME`, `EW_COMMAND_GROUP_NAME`, `EW_DISPATCH_CONCURRENCY`, `EW_PUBLISH_LEASE_SECONDS`는 삭제되었으며 YAML/env에 남으면 시작 시 ConfigurationError로 제거를 안내한다. ingress 동시성 정본은 `EW_INGRESS_CONCURRENCY`, `EW_CONCURRENCY`는 같은 값의 호환 별칭이다. graph 실행 총한도는 `AGENT_WORKER_CONCURRENCY`다.

기존 EW 테이블·frozen migration·과거 성능 원본은 이행/감사 목적으로 보존했다. 격리 PostgreSQL17(63366)·Redis7(63367)만 사용한 뒤 전용 컨테이너2개를 제거하고 부재를 확인했다. 기존 checkout·.env·서비스 컨테이너·실제 DB·Redis group은 변경하지 않았다. 상세 삭제/보존 목록은 [legacy-cleanup-evidence.json](legacy-cleanup-evidence.json)을 따른다. 실제 LLM 및 Executor Python 실행·처리량 측정·배포 전환은 포함하지 않는다.
