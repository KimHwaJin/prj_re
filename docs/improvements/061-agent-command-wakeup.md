# 061 Worker 깨우기 신호와 유휴 조회

| 항목 | 내용 |
|---|---|
| 상태 | 구현·기본/실제 DB/독립 프로세스 검증 완료 / 베이스 미병합·미배포 |
| 시작일 / 완료일 | 2026-10-03 / 2026-10-03 |
| 브랜치 | feature/agent-command-wakeup |
| 기준 commit | 799e078 — 공통화 후 소규모 실행 자리 비교 완료 |
| 합의 | 구현 계획4단계, D-07/D-09 |

## 문제

공통 graph 슬롯을 도입했지만 빈 명령 queue도 기본0.25초마다 claim query로 확인했다. Pod마다 빈 조회가 늘어나며 새 요청은 다음 polling까지 기다릴 수 있었다. 기존 SSE listener는 프론트 구독이 있어야 유지되어 Worker가 그대로 의존할 수 없었다.

## 변경과 동작

DB command trigger의 커밋 연계 NOTIFY, Worker wake/timer/작업 완료/주기 scan을 조합했다. 명령과 session owner/FIFO/총한도는 DB 정본을 유지한다. full 슬롯에서는 알림에 반응해 빈 claim/busy loop를 하지 않는다.

명령 추가/재예약/완료는 trigger가 알리므로 API와 psycopg router의 별도 알림 구현이 갈리지 않는다. rollback은 알리지 않는다. retry 시각은 claim 시작 기준의 indexed MIN으로 확인하여 query 도중 deadline이 지나도 놓치지 않는다. 신호 유실은 기본5초 reconcile, 연결이 없으면 기존0.25초 polling을 사용한다.

SSE와 Worker는 `service_runtime.postgres_signals`를 통해 프로세스당 LISTEN1개를 공유한다. 구독 참조가 각 수명을 소유하므로 프론트 없음/마지막 SSE 종료에도 Worker 연결을 유지하며, Worker 종료 후 SSE도 독립 유지 가능하다. 연결이 끊기면 invalidate/reconnect하고 구독자 한쪽 callback 오류는 다른 쪽 전달을 끊지 않는다.

API/SSE 규격·Agent graph/prompt·LLM 호출·동일 세션 잠금·Executor Redis 수신 계약은 바꾸지 않았다. 새 설정은 기존 중앙 loader로 통일했고 별도 role env 주입을 추가하지 않았다. 기존 SSE 전용 listener loop의 중복 connect/reconnect 코드는 삭제했다.

## 검증과 측정

[보고서](../reports/worker-wakeup-2026-10-03/README.md)에 회귀·실제 DB/Redis·프로세스 경합과 측정 근거를 기록한다. notify false/true를 같은 현재 Worker·DB에서 비교하여 공통화 자체의 효과와 신호 효과를 분리한다. idle empty claim은 전체 서비스 SQL과 다르다. 여러 시험의 중복 개수를 고유 총수로 합산하지 않는다.

최종 기본625개 통과/365개 조건부 skip, 실제 DB·Redis·SSE 회귀55개 통과, 알림 재확인19개·DB 연결/동시성20개·독립 OS 프로세스 검사1개가 통과했다. 서로 중복되는 회귀를 고유 총수로 합산하지 않는다. 6초 유휴 빈 claim은20→1회로95% 줄었고, 전체 서비스 SQL/처리량 감소율이 아니다.

## 제한과 다음

- UI 없는 경우 LISTEN 연결은0개에서1개로 늘어난다. UI가 있으면 기존 SSE1개와 공유해2개로 늘리지 않는다. DB 예산에 pool 밖의1개를 포함한다.
- signal과 SQL 모두 장애 시 영구적 진행을 보장하는 기능이 아니다. 기존 writer가 불확실하면 원래 RECOVERY 보호를 유지한다.
- 실제 Kubernetes/HPA·대량 fan-out·장기 큰 원장·역할별5초 모델 및1/10/30/50명 전체 분석 E2E 비교는 단계5다. 이번 작은 유휴 측정을 서비스 최대 처리량으로 환산하지 않는다.
- 실제 서비스 DB/.env/컨테이너/Redis group과 original checkout을 변경하지 않는다. 구현·검증용 파생 브랜치에서만 commit하며 사용자 요청 전 베이스 merge/push는 하지 않는다.

파일·설정·수명·migration과 인수인계는 [알림 안내](../agent-command-wakeup.md)를 따른다.
