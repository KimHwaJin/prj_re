# Run SSE 사용·운영 기준

기존 `GET /api/v1/sessions/{session_id}/runs/{run_id}/stream`과 상태 GET을 유지한다.
`X-User-Id`와 자원 소유권 확인, `Last-Event-ID`, `id`/`event`/`data` 형식은 동일하다.
프론트는 SSE로 상태를 받고 연결이 끊기면 마지막 event id로 재접속한다.
`run.state`는 상태 snapshot이며 이벤트 id가 없다. `: heartbeat`는 데이터 변경이 아니다.

## 전달 방식

1. Run/Task 상태나 durable 이벤트가 PostgreSQL에 저장된다.
2. 같은 트랜잭션의 trigger가 커밋 후 공개 Run ID 변경 알림을 보낸다.
3. 각 API 프로세스의 LISTEN 연결이 해당 Run의 공유 조회를 무효화하고 대기를 깨운다.
4. 소유권·활성 여부를 확인한 후 DB의 상태·이벤트를 읽고 각 SSE cursor 뒤부터 전달한다.

롤백은 알림을 보내지 않는다. 알림에 사용자 메시지·토큰·결과를 싣지 않는다.
다른 프로세스/Pod의 저장도 같은 DB에 연결된 listener에 전달된다. 같은 프로세스에서
동일 사용자/세션/Run/커서 조회는 공유하지만, Pod 간 캐시를 공유하는 구조는 아니다.
프론트가 여전히 GET을 반복하면 그 조회량은 이번 변경으로 줄어들지 않는다.

## 설정과 자원

중앙 config > env > 기본값 규칙을 따른다.

| 설정 | 기본값 | 의미 |
|---|---:|---|
| SSE_RECONCILE_INTERVAL_SECONDS | 15초 | 변경 알림 누락/장애 시 Run별 DB 재확인 |
| SSE_POLL_INTERVAL_SECONDS | 0.5초 | 변경 알림 병합 및 연결 종료 점검 주기. 유휴 DB polling 주기가 아님 |
| SSE_HEARTBEAT_SECONDS | 15초 | SQL 없는 SSE heartbeat |
| SSE_EVENT_BATCH_SIZE | 100 | 한 번에 읽는 durable event 수; 전체 배치면 즉시 다음 페이지 조회 |
| SSE_MAX_CONNECTIONS | 1000 | 프로세스별 활성 SSE 연결 상한. 초과 시 503 + Retry-After: 5 |

활성 구독이 있는 프로세스당 **별도 PostgreSQL LISTEN 연결 한 개**를 사용한다.
API SQLAlchemy 풀에서 하나를 장기간 빌리지 않는다. 마지막 SSE가 닫히면 listener도
종료한다. Pod 내 프로세스 수·replica 수가 늘면 이 연결도 늘어난다. 전역 DB 연결
상한을 보장하는 설정이 아니므로 기존 연결 예산에 포함해야 한다.

동일 프로세스에서 최대 64페이지·UTF-8 본문 합계 8 MiB를 공유 캐시한다.
이는 Python 객체/연결 버퍼를 포함한 전체 RSS 한도가 아니다. 큰 페이지는 캐시하지 않고
각 소비자가 필요할 때 다시 읽는다. 느린 클라이언트별 무한 이벤트 큐를 두지 않는다.
중복 알림은 기존 병합 간격 안에서 합친다. 지속적인 토큰 출력이 있으면 실제 변경을
전달하기 위한 조회가 발생하며, 유휴 구간에도 15초마다 보조 조회가 발생한다.

조회가 끝나면 DB 세션/트랜잭션을 반환한 뒤 네트워크로 보낸다. 클라이언트가 느리거나
Executor가 일주일 실행돼도 SSE 대기 때문에 API DB 풀의 연결을 계속 잡지 않는다.

## 장애·재접속·권한 변경

LISTEN 연결 실패 시 재접속하며 보조 조회를 유지한다. 새 LISTEN 연결을 만들면 기존
구독을 다시 읽으므로 시작/재접속 전후의 알림 공백도 보완한다. 변경 알림은 비영속이며
이벤트 DB·cursor가 복구 근거다. 누락된 알림은 보조 주기와 DB 지연만큼 전달이 늦어질 수 있다.

사용자/프로젝트/세션 접근 변경은 공유 캐시를 무효화한다. 읽을 때 활성 사용자·활성
프로젝트·세션 소유권을 재확인하며 권한이 사라진 스트림은 종료된다. 이미 전송한
데이터는 회수하지 않는다. 재접속 시 기존 HTTP 인증/접근 검사를 다시 거친다.

배포 전에 Alembic CRUD migration `20260929_0022`를 적용한다. trigger 설치가 빠지면
보조 조회만 작동하여 변경 전달이 느려진다. 새 앱 시작만으로 migration을 실행하지 않는다.
LISTEN은 세션 연결이 필요하며 운영 PostgreSQL/프록시 정책을 확인해야 한다.
현재 listener TLS 옵션은 기존 API asyncpg 연결 정책(ssl=False)에 맞췄다.

변경 전후 측정과 제한은 [026 기록](improvements/026-run-sse-notifications.md)을 참고한다.

루트 `app.py`의 서버는 SIGTERM을 받으면 HTTP drain 전에 SSE 구독을 깨워 종료한다.
별도의 ASGI/플랫폼 launcher는 이 종료 hook을 연결하거나 유한한 HTTP 종료 기한을
설정해야 한다. 무기한 SSE가 있는 서버에서는 lifespan 종료만 기다리는 것으로 충분하지 않다.

SSE 앞단의 토큰 저장 주기·버퍼 상한·DB 저장 장애 정책은
[027 토큰 이벤트 버퍼 기록](improvements/027-token-event-buffer.md)을 참고한다.
토큰 저장 간격과 SSE 변경 알림 병합 간격은 별개다.
