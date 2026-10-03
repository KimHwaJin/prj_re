# 공통 Agent Worker 알림과 대기

061은 공통 명령 원장의 정확성을 유지하면서 유휴 claim 비용과 접수 후 polling 대기를 줄인다. 공개 Run/API/SSE 규격, Agent graph·prompt·LLM 호출, 세션 입력 잠금과 Executor Redis 계약은 바꾸지 않았다.

## 저장과 알림

CRUD migration `20261003_0027`이 `agent_commands`에 trigger를 설치한다. API 접수, psycopg Inbox routing, command outcome, backfill 모두 같은 DB trigger를 거친다. 명령 추가와 READY 재예약, DONE/IGNORED/FAILED 전환을 알리고 단순 RUNNING 전환/heartbeat는 알리지 않는다. 동일 namespace의 알림은 transaction/Event에서 합쳐진다.

PostgreSQL NOTIFY는 transaction commit 후 전달되고 rollback은 전달하지 않는다. channel은 `dtest_agent_command_changed`, payload는 namespace의 MD5 digest다. 작업 ID·입력·실행 권한을 담지 않는다. digest는 필터용 힌트이며 명령 claim은 원래 namespace/FIFO/owner token/SKIP LOCKED 조건으로 수행한다. 알림 자체를 실행 queue로 사용하거나 ACK하지 않는다.

## Worker 대기 규칙

1. 시작과 LISTEN 연결/재연결 때 DB를 확인한다. 알림과 SELECT 사이 경합을 막기 위해 wake를 claim **직전**에 비우며, query 도중 도착한 알림은 다음 대기에서 유지한다.
2. 자리가 있으면 순차 claim해 한도를 채운다. 자리가 꽉 차면 active 작업/종료만 기다린다. 알림이 계속 와도 추가 claim이나 busy loop를 하지 않는다.
3. 빈 claim이고 LISTEN이 정상일 때만 다음 미래 READY 시각을 indexed MIN 조회한다. claim 시작 시각을 기준으로 하여 deadline이 조회 도중 지나도 짧은 timer로 다시 확인한다. 이미 그전에 지난 부적격 deadline은 반복 timer 대상에서 제외한다.
4. 명령 알림·작업 완료·재시도 시각·느린 reconcile 중 먼저 오는 계기로 확인한다. 여러 Pod가 깨도 DB가 한 소유자만 정한다. 각 프로세스에서 힌트를 합치지만 별도 알림 전달 공정성/Pod 예약 배분을 추가하지 않았다.
5. LISTEN이 미연결/끊김/비활성일 때는 기존 짧은 polling을 사용한다. 신호 유실은 느린 reconcile로 보완한다. listener 장애만으로 공개 Run을 실패시키거나 process execution health를 오염시키지 않는다.

future 시각 조회는 blocked session까지 포함할 수 있는 힌트다. 실제 실행 가능 여부는 원래 claim query로 확인한다. graph 결과가 불확실할 때 RECOVERY/token을 자동 탈취하는 동작은 추가하지 않는다.

## 공용 LISTEN 연결의 수명

`service_runtime/postgres_signals.py`가 DB 정본/프로세스 event loop별로 연결1개를 공유한다. command와 Run SSE channel을 함께 LISTEN하며 callback과 구독 수명은 독립적이다. listener task는 알림 연결을 관리하며 graph 실행 Worker를 추가하는 것이 아니다.

- Worker 구독이 유지되면 프론트가 없어도 listener가 동작한다. SSE가 붙어도 연결을 추가하지 않는다.
- 마지막 SSE가 끊겨도 Worker의 연결을 닫지 않는다. Worker가 종료되어도 살아 있는 SSE 구독은 유지한다.
- 마지막 참조가 나가면 listener task를 취소하고 close를 관찰한다. 반복 취소에서도 정리 task를 버리지 않는다.
- 연결 성공/재연결 때 구독자를 invalidate한다. 실패는0.5초부터 최대5초까지 reconnect backoff를 적용한다. 연결 timeout5초·close timeout2초를 사용하며 오류 유형만 기록한다.
- 명시한 fixture/connector를 사용하는 RunStreamHub는 독립 broker를 만들어 다중 프로세스·연결 장애를 분리해 검사할 수 있다. 실제 기본 app은 공용 broker를 쓴다.

기존 UI 없는 유휴 프로세스는 LISTEN0개였고, 이제 Worker 알림을 켜면 **1개**를 유지한다. UI가 있는 경우는 기존 SSE1개를 함께 쓰므로2개가 되지 않는다. CRUD/Event/checkpoint pool을 checkout하는 연결이 아니라 pool 밖의 연결1개다. startup summary의 예산 이름은 `notification_listener`로 통일했다. 전체 DB 연결 예산에는 이1개를 포함해야 한다.

## 설정

기존 중앙 loader의 YAML > env > 기본값과 불변 snapshot을 유지한다. 역할별 별도 설정 파일/중복 Redis 설정은 추가하지 않았다.

| 설정 | 기본값 | 의미 |
|---|---:|---|
| `AGENT_WORKER_NOTIFY_ENABLED` | true | Worker 알림 구독. false이면 기존 polling 방식이며 SSE의 알림 구독은 독립 유지 |
| `AGENT_WORKER_RECONCILE_INTERVAL_SECONDS` | 5.0 | 정상 listener에서 힌트 유실을 보완하는 유휴 재확인 주기. timer 유효 최소0.05초 |
| `AGENT_WORKER_POLL_INTERVAL_SECONDS` | 0.25 | listener 미연결/비활성 시 fallback 주기. 기존 값 보존, 유효 최소0.05초 |
| `AGENT_WORKER_CONCURRENCY` | 기존 값 유지 | 모든 명령 종류가 함께 사용하는 graph 한도. 알림 연결과 별개 |

NOTIFY가 유실되고 다른 wake/작업 완료가 없다면 다음 정상 reconcile까지 기다릴 수 있다. DB 응답/이벤트 루프 지연은 추가된다. 재시도 timer가 있으므로 정상적인 미래 READY를 항상5초씩 기다리는 것은 아니다. SSE heartbeat/재확인 설정과 Executor Inbox의 polling/metrics는 이번 작업에서 변경하지 않았다.

## 파일과 배포

- `service_runtime/postgres_signals.py`: 재사용 가능한 공용 listener·callback 격리·참조/재연결 수명.
- `api_service/runs/commands/wakeup.py`: namespace 힌트 필터·다음 retry 시각.
- `api_service/agent_run_worker.py`: graph 슬롯·claim·신호/timer/완료/종료 대기 조립.
- `api_service/services/run_stream_service.py`: 기존 SSE 읽기·cursor·cache는 유지하고 listener 수명만 공용 broker로 연결.
- `crud_migrations/versions/20261003_0027_command_notifications.py`: trigger/function 추가. downgrade는 해당 trigger/function만 제거한다.

배포 전에 API/명령 DB에서 `alembic -c alembic.crud.ini upgrade head`를 수행한다. `ew_0001`이나 checkpoint schema를 변경하는 작업은 아니다. 이 요청에서는 실제 서비스 DB migration, 기존 컨테이너 재기동과 베이스 merge/push를 수행하지 않았다. 기존060의 DB 이행/backfill·구 실행기 종료 조건은 별도로 지킨다.

[구현 기록](improvements/061-agent-command-wakeup.md), [검증/유휴 비용 보고서](reports/worker-wakeup-2026-10-03/README.md)를 따른다. Kubernetes/HPA·대규모 fan-out 비용과 실제 분석 E2E 처리량의 전체 단계5 검증은 별도다.
