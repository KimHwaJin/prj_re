# Worker 깨우기 신호·유휴 조회 검증 — 2026-10-03

**공통 Worker의 유휴6초 빈 claim이20회에서1회로 줄었다.** 동일한 현재 Worker에서 알림을 끄고/켜서 비교했다. 이전 공통화의 자리 공유 효과와 분리한 측정이며 전체 서비스 SQL·CPU·포화 처리량의95% 개선을 의미하지 않는다. 최종 연결 반환 점검에서 retry 시각 조회에도 기존 cancellation-safe short_session을 적용하고 관련3개 실제 DB 사례를 다시 통과했다.

구현은 `feature/agent-command-wakeup`, 기준은 `799e078`이다. [061 기록](../../improvements/061-agent-command-wakeup.md), [알림/수명/설정 안내](../../agent-command-wakeup.md)를 따른다.

## 결과

| 검증 | 결과 | 증거 |
|---|---|---|
| 최종 API·Agent 기본 회귀 | 625 passed / 365 skipped / 74 warnings / 26.37초 | [unit-regression.txt](unit-regression.txt) |
| 원장·실제 PG/Redis·기존 SSE·소유권·종료 회귀 | 55 passed / 71.41초 | [postgres-regression.txt](postgres-regression.txt) |
| callback 격리 보완·6초 측정 후 wakeup 핵심 재확인 | 19 passed / 28.73초 | [wakeup-recheck.txt](wakeup-recheck.txt) |
| 공유 구독자 오류·정리 격리 | 13 passed / 1.08초 | [subscriber-isolation.txt](subscriber-isolation.txt) |
| 실제 독립 Python 프로세스2개: 명령6개 중복 없이 완료 | 1 passed / 7.14초 | [multiprocess.txt](multiprocess.txt) |
| retry 조회도 cancellation-safe short_session으로 통일한 후 재확인 | 3 passed / 18 deselected / 7.38초 | [short-session-final.txt](short-session-final.txt) |
| 짧은 transaction·풀 반환·실행 한도/HITL/동시성 | 20 passed / 50.80초 | [resource-boundaries.txt](resource-boundaries.txt) |

여러 행의 검사들이 겹치므로 고유 총수로 합산하지 않는다. skip365는 전용 DB/모델 등 조건이 없는 기본 실행의 조건부 검사이며 실제 DB 재실행과 구분한다. 기본 회귀의 test 시간은 서비스 응답 시간이나 처리량 지표가 아니다.

## 유휴 측정

같은 PostgreSQL·현재 공통 Worker·fallback250ms·reconcile5초에서 측정했다. startup LISTEN 확인/initial scan을 먼저 마친 후6초 구간만 계측했다. 상태 완료 SQL polling은 이 유휴 구간에 수행하지 않았다. fixture는 실제 DB에 접속하는 기존 NullPool이며, worker.claim_one 호출을 세어 빈 query를 확인한다.

| 조건 | 유휴6초 빈 claim | 명령 제출 시작→claim | 유휴 LISTEN 연결 |
|---|---:|---:|---:|
| 알림 끄기 | 20회 | 303.6ms | 0개 |
| 알림 켜기 | 1회 | 204.3ms | 1개 |

빈 claim은20→1로95% 줄었다. 정상 listener에서도5초 주기 확인이 남으므로0회만을 목표로 하지 않는다. 현재는 빈 claim 뒤 indexed future retry 시각 조회도 실행하므로 claim 수가 전체 SELECT 수와 같지 않다. Event Inbox/router·metrics·취소 감시·SSE 읽기는 이번 claim 계수에서 제외한다.

제출→claim은 각 조건의 단일 참고 sample다. 사전 생성한 세션에 실제 ASGI 요청을 제출하기 직전부터 immutable claim 반환까지이며, 요청 검증/DB 접수와 연결 생성·스케줄링이 포함된다. graph 진입/전체 실행/프론트 표시 시간이 아니다. 신호 방식이 빠르게 반응하는 경로를 확인했지만 약100ms 차이를 평균/p95·성능 보장으로 일반화하지 않는다.

첫1.2초 sample은4→0 빈 claim이었으나 주기 scan을 포함하도록6초로 늘려 최종 측정했다. [초기 참고값](idle-first-1.2s.json)은 숨기지 않되 [최종6초 원본](idle-measurement.json)과 섞어 평균을 만들지 않는다.

## 정확성과 수명

- 실제 두 개의 독립 LISTEN 연결에 commit만 전달되고 uncommitted/rollback/다른 namespace 신호는 전달되지 않았다. psycopg Inbox routing도 동일 trigger로 알림을 생성한다.
- claim SELECT와 sleep 사이 신호를 놓치지 않으며 full 슬롯의 연속 알림은 추가 claim/busy loop를 만들지 않는다. private broker2개의 fan-out에서도 각 명령은1회 실행한다.
- 명령 신호를 의도적으로 버려도 periodic scan으로 완료했다. 연결을 종료한 동안 커밋된 작업도 fallback으로 실행하고 재연결 후 계속 진행했다.
- 미래 READY retry는5초 reconcile보다 먼저 deadline timer로 재검사하며 deferral은 업무 실패 횟수를 소모하지 않았다. query 도중 지나간 deadline도 놓치지 않았다.
- 실제 OS 프로세스2개의 별도 엔진/LISTEN에서 namespace 신호를 받아 총6개 사용자 명령이1회씩 완료되고 정상 drain했다. Kubernetes Pod/HPA 시험과는 다르다.
- Worker와 기본 app SSE가 실제 같은 connection을 공유했다. 마지막 SSE가 나가도 Worker reference가 유지되며 Worker가 끝난 뒤 SSE-only도 시작할 수 있다. 마지막 reference가 나가면 connection/task를 종료했다.
- callback 하나의 오류가 다른 구독자의 전달/구독 정리를 끊지 않으며 반복 cancellation도 close 관측과 owned graph 정리를 버리지 않았다.
- 실제 Redis→Inbox→원장→graph/checkpoint resume와 public Run/SSE 완료, token/RECOVERY 보호, HITL/Executor 대기의 실행 자리 반환, 짧은 transaction 경계는 기존 실제 DB 회귀로 확인했다.

## 연결 비용과 제한

UI 없는 경우 알림을 끄면0개, 켜면 pool 밖 LISTEN1개가 늘어난다. UI가 있는 경우 기존 SSE 연결1개와 공유하여2개가 되지 않는다. listener가 command/SSE의 업무 DB transaction을 길게 붙들지는 않는다. 접속 예산에는 이1개를 포함한다.

정상 연결의 기본 reconcile5초는 유실 신호만 있고 다른 작업 완료/wake가 없으면 최대 그 주기와 DB/스케줄링 지연을 기다리는 경로다. listener가 미연결이면 기존250ms fallback을 사용한다. full 상태의 graph/LLM 처리 시간을 줄이는 기능이 아니다. 대량 idle Pod에 broadcast되는 NOTIFY의 비용·공정성, 장기 큰 원장과 전체1/10/30/50명 E2E는 전체 단계5로 남긴다.

실제 LLM API·Executor Python/Jupyter/PVC 대용량 처리와 Kubernetes HPA를 실행하지 않았다. 독립 Worker child는 고정200ms graph 출력, 원장/체크포인트 E2E도 fixture graph/결과를 사용한다. 실제 서비스의 사용자 흐름 전체 처리량으로 환산하지 않는다.

## 격리와 재현

이 요청의 전용 PostgreSQL17(63370)·Redis7(63371), 삭제 가능한 identity_test만 사용했다. 기존 서비스 DB·Redis group·.env·compose 컨테이너·original checkout은 변경하지 않았다. 기존 Python3.11 venv/lock을 쓰고 추가 의존성은 설치하지 않았다. migration0027은 전용 DB에만 적용했다. [설정·검증 근거](evidence.json), [자원 정리](cleanup.json)를 남긴다.

전용 DB와 localhost Redis 환경변수를 설정한 후:
```sh
PYTHONPATH=src .venv/bin/python -m pytest -q \
  src/api_service/test/test_command_wakeup_postgres.py \
  src/api_service/test/test_agent_commands_postgres.py \
  src/api_service/test/test_run_stream_notifications.py \
  src/api_service/test/test_session_execution_postgres.py \
  src/api_service/test/test_graceful_shutdown_postgres.py --disable-warnings
```

`DTEST_IDENTITY_TEST_DATABASE_URL`은 삭제 가능한 localhost identity_test의 asyncpg URL, `DTEST_COMMAND_TEST_REDIS_URL`은 전용 Redis URL이다. fixture는 해당 DB schema를 migration/reset한다. `DTEST_COMMAND_WAKEUP_REPORT`로6초 비교 JSON 출력 경로를 지정할 수 있다. 테스트 fixture를 운영/공유 DB에 연결하지 않는다. 최종 기본 회귀는 기존 API·Agent 경로에 대해 전용 DB 환경변수 없이 실행했다.
