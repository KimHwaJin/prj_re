# 026 — Run SSE 변경 알림·공유 조회

- 날짜: 2026-09-29
- 브랜치: feature/run-sse-notifications
- 기준: 08a6809 (025 베이스 병합 완료)
- 구현·검증 commit: `9c51953`
- 검증 후 이번 작업의 일회용 PostgreSQL 컨테이너·볼륨 정리 완료.
- 상태: 구현·격리 PostgreSQL/실제 HTTP 비교/전체 회귀/패키지 검증 완료. 베이스 병합·push·배포 미수행.

## 문제와 범위

기존 SSE는 연결마다 기본 0.5초 간격으로 세션·공개 상태·이벤트를 SELECT했다.
021은 반환 데이터 크기를 줄였으나 조회 빈도는 유지했다. 변경이 없는 장기 Executor
대기와 동일 Run을 보는 여러 탭에도 같은 DB 조회가 반복되었다.

공개 GET/SSE 규격 및 Last-Event-ID 유지, 변경 알림과 느린 누락 복구, 같은 프로세스의
동일 소유자/세션/Run/커서 조회 공유를 구현한다. 프론트 직접 GET polling, Run 접수,
Agent/LLM/Executor 업무 흐름은 이번 범위가 아니다.

## 구현

- Alembic 0022: agent_runs 상태, tasks 상태, task_events INSERT, 접근 권한에 영향을 주는
  users/projects/sessions 변경에서 트랜잭션 결합 PostgreSQL NOTIFY. 커밋되어야 전달되며
  롤백은 전달되지 않는다. 알림에는 공개 Run UUID 또는 전체 권한 캐시 무효화 표시만 있다.
  이벤트·상태 본문은 기존 DB가 원본이다. 기존 heartbeat/lease 갱신에는 알림을 만들지 않는다.
- RunStreamHub: 활성 SSE가 있는 프로세스당 전용 LISTEN 연결 1개. 마지막 구독 종료 시
  닫고, 장애 시 재접속한다. 새 연결/재접속 시 모든 구독을 다시 읽어 초기 조회와 LISTEN
  사이의 공백을 보완한다. PostgreSQL 연결 실패 동안도 보조 조회를 유지한다.
- 동일 사용자·세션·공개 Run·generation·커서에서 DB 읽기를 합친다. 프로세스 공유 캐시는
  최대 64페이지/UTF-8 본문 합계 8 MiB다. 느린 소비자를 위한 무한 큐를 만들지 않는다.
  프로세스 SSE 연결 상한은 기본 1000. 한도 초과는 응답 헤더 전에 503/Retry-After를 반환한다.
- 각 연결은 독립적인 마지막 이벤트 번호를 유지한다. 과거/느린 커서는 DB에서 페이지로
  재생하며 pagination은 즉시 진행한다. 알림이 여러 번 와도 DB sequence가 재생 기준이다.
- 상태 변경 알림 폭주는 기존 SSE_POLL_INTERVAL_SECONDS(기본 0.5초) 내 합쳐 읽는다.
  이 설정은 이제 변경 알림 병합·연결 상태 점검에 사용하며 유휴 DB polling 주기가 아니다.
- SSE_RECONCILE_INTERVAL_SECONDS 기본 15초에 Run별 보조 조회. heartbeat 기본 15초는
  SQL 없이 전송한다. 실행 중간 HITL·Executor 대기에도 연결을 유지한다.
- 공유 조회 시마다 사용자 활성·세션 소유권·프로젝트 활성·Run 귀속을 다시 검사한다.
  권한 변경 알림과 주기 확인에서 캐시를 폐기한다. DB 세션은 조회 후 전송 전에 반환한다.
- 실제 HTTP 시험에서 기존 중첩 인증 dependency의 get_db가 request 수명으로 남아
  SSE 연결당 idle transaction을 유지하는 문제를 확인했다. SSE 전용 인증 dependency에서
  하위 get_db까지 function scope로 지정해 헤더 전 반환한다. 기본 API 인증 정책은 유지한다.
- bootstrap 종료/HTTP disconnect/send 실패/직접 generator 종료에서 구독 및 listener 정리.
  루트 서버 SIGTERM hook에서 HTTP drain 전에 SSE 대기를 깨운다. 실제 HTTP 시험은
  build_server의 종료 hook을 호출하며 실제 OS SIGTERM은 기존 subprocess 회귀가 검증한다.

## 검증·관측

실제 로컬 PostgreSQL, 별도 listener와 별도 프로세스 writer, 실제 loopback HTTP SSE를
사용한다. 외부 LLM·Executor·Redis와 기존 실행 중인 서비스는 사용하지 않는다.
집중 검증 25건을 통과했다. commit/rollback, 별도 프로세스 저장, 재접속·알림 누락,
커서 재생, 권한 변경, 캐시·접속 상한, 알림 폭주, DB 풀 1개에서 실제 HTTP 동작,
서버 종료 및 구독 정리를 검사했다. 전체 회귀 결과는 아래에 기록한다.
1·10·30·50 연결에서 서로 다른 Run/동일 Run 탭을 분리해 변경 전후 유휴 SELECT와
변경 전달 지연을 측정한다. 3초 관측창/1회 시도이므로 운영 용량 또는 p95 SLA가 아니다.

## 제한

- SSE 구독이 있는 **프로세스마다 DB 연결 1개 추가**. API SQLAlchemy 풀과 별도이며
  replica 전체 상한은 아니다. 다중 Uvicorn worker를 쓰면 각각 추가된다.
- NOTIFY 자체는 비영속 알림이다. 실제 데이터 복구는 이벤트 DB와 커서, 느린 재조회로 한다.
- 캐시는 프로세스별이고, 같은 Run을 서로 다른 Pod에서 읽으면 Pod별 조회가 발생한다.
- DB trigger는 작은 쓰기 비용을 추가한다. 알림 채널 문제/DB 연결 한도는 운영에서 확인한다.
- 외부 Kubernetes·폐쇄망 DB 연결 정책·프록시 경로는 미검증. LISTEN은 session 단위
  연결을 요구한다. 마이그레이션을 배포 단계에서 적용해야 한다.

## 변경 전후 비교

[1·10·30·50 연결 비교 보고서와 원본](../reports/run-sse-notifications-2026-09-29/README.md)을 남겼다.
서로 다른 Run 50개의 3초 유휴 SELECT는 427 → 0회, SSE가 유지한 열린 유휴 트랜잭션은
50 → 0개였다. 같은 Run 50개 탭의 상태 전달 평균은 961.4 → 57.2ms였다. 이는 짧은
유휴 관측 및 단일 프로세스 조회 공유 효과이며 Agent 전체 실행 시간 개선치가 아니다.

패키지 wheel을 소스 checkout 밖에서 검증했다. 공개 API 34개 경로와 요청·응답·인증
OpenAPI 계약이 동일함을 확인했다(설명 문구 제외). 설치 패키지에서 7개 역할 Agent를
생성하고 mock 그래프 6단계를 실행했으며 외부 서비스는 호출하지 않았다.

## 최종 검증 결과

- 전체 API·Agent 회귀: **578 passed, 2 subtests passed**, 53 warnings, 210.70초.
- SSE·graceful shutdown 집중 검증: **25 passed**, 13.60초.
- PostgreSQL fixture에서 migration upgrade → downgrade → upgrade 왕복 통과.
- 변경 Python 파일 8개의 문법 검증 및 `git diff --check` 통과.
- OpenAPI 34개 경로의 요청·응답·인증 명세 동일(설명 제외).
- 최종 wheel 격리 smoke 통과: 역할 Agent 7개 생성, mock 그래프 6단계 실행.
- 비교 측정: 이전 8조합·이후 8조합(1/10/30/50 연결 × 서로 다른 Run/동일 Run).
- 테스트용 PostgreSQL만 사용했다. 기존 앱 서버·외부 Redis·실제 LLM/Executor는 변경·호출하지 않았다.

실행 명령은 `PYTHONPATH=src`와 일회용 로컬 DB를 지정한 뒤 다음과 같다.

```sh
python -m pytest src/api_service/test src/agent_service/agents/analysis/tests -q --disable-warnings --maxfail=2
python -m pytest src/api_service/test/test_run_stream_notifications.py src/api_service/test/test_graceful_shutdown.py -q --disable-warnings --maxfail=1
```

[검증 메타데이터](../reports/run-sse-notifications-2026-09-29/validation.json)
