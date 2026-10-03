# 060 DB 명령 원장과 공통 Agent Worker

| 항목 | 내용 |
|---|---|
| 상태 | 구현·관련 회귀·격리 PostgreSQL/Redis 검증 완료 / 베이스 미병합·미배포 |
| 시작일 / 완료일 | 2026-10-03 / 2026-10-03 |
| 브랜치 | feature/unified-agent-command-worker |
| 기준 | faca5b1 — 059 Run 실행 경계 |
| 구현 commit | 67c8dd0325086b6cf433f617e5517231090dab3d (로컬, 베이스 미병합·원격 미게시) |
| 합의 | D-06/D-08/D-09, C-02/C-03 |

## 문제

사용자 입력은 API DB Run dispatcher가, Executor 결과는 Redis 내부 command dispatcher가 실행했다. 한 프로세스 안에서도 실행 자리·claim 정본이 둘이었고 API/이벤트 DB가 달라질 수 있었다. 한쪽 실행 자리가 비어도 다른 입력의 처리량에 사용할 수 없었다. 내부 Redis 메시지 ACK/lease와 실제 graph 수명이 묶여 있었다.

## 변경

API DB에 `agent_commands`를 추가했다. 기존 공개 Run과 private invocation, checkpoint ID는 보존한다. 새 요청·사용자 승인·Executor 결과를 내부 명령으로 기록하고 **한 Agent Worker가 공통 총한도**로 실행한다.

- 접수+Task+invocation+queued 이벤트+명령은 한 transaction이다. Inbox routing+binding sequence+명령도 한 transaction이다. 외부 원본 Redis는 Inbox commit 후 ACK한다.
- 세션별 삽입 advisory lock과 ordinal, 선행 미종료 명령 조건으로 FIFO를 보장한다. 미래 재시도 명령도 뒤 명령이 추월하지 않는다. 다른 세션은 독립적으로 실행된다.
- DB claim과 session ownership을 함께 확정하며, heartbeat 만료로 writer를 자동 탈취하지 않는다. 취소/불확실한 결과는 token·RECOVERY 보호를 남긴다.
- HITL/Executor 대기까지 완료한 graph 호출은 명령 DONE과 자리 반환이다. 공개 Run 업무 상태와 WAITING_EXECUTOR 입력 잠금은 별개로 유지한다.
- 이벤트 수신 bootstrap에서 graph callback·별도 Dispatcher/SessionGuard/Outbox 생성을 제거했다. 기존 Redis 외부 이벤트 계약은 유지하고 새 내부 Redis command 발행/소비를 중단했다.
- 실행 활성 상태의 API/Inbox 분리 DB 설정은 명시적 오류로 거절한다. 데이터 이관을 자동 수행하거나 조용히 대상 DB를 바꾸지 않는다.
- 기존 작업 backfill은 원래 Run/command ID를 보존하며 멱등하다. 구 실행 중 소유권이나 순서를 알 수 없는 부분 이행을 거절한다. readiness에서도 누락된 이전 대기 작업을 감지한다.
- Task 진단 응답 blocking_reasons에 `unfinished_command`를 추가하여 미종료 내부 명령의 잠금 사유를 표시한다.
- metrics의 command 상태 정본은 `agent_commands`로 바꿨다. 예전 별도 graph 한도 환경변수는 현재 예제에서 제외하고 잔여 진단 필드는 deprecated로 설명했다.

파일·schema·상태·설정·DB 수명·배포 전환은 [인수인계 안내](../agent-command-worker.md)를 따른다.

## 검증

최종 결과는 [검증 보고서](../reports/agent-command-worker-2026-10-03/README.md)에 기록했다. 기본608개 통과/358개 조건부 skip, 넓은 실제 DB 회귀204개 통과 뒤 진단 오류1건 수정, 관련63개 재검증 및 최종 원장/Redis16개 통과다. 서로 중복되는 검사 개수를 고유 총수로 합산하지 않는다. 전용 PostgreSQL·Redis에서 원자 접수/routing, 멱등·sequence gap, 동일 세션 순서·재예약, 여러 동시 claim, 공통 한도2의 사용자/이벤트 혼합 실행, checkpoint 재개·공개 Run/SSE 투영, 중복 재전달, claim 후 중단·취소·owner token 검사를 수행한다. 실제 모델과 실제 Executor Python 실행은 포함하지 않는다.

공통 한도 준수와 빈 자리 재사용을 검증했으며 처리량 향상률을 아직 측정하지 않았다. 예전 API32+Event4와 새32를 비교하면 용량 조건이 달라진다. 전후 동일 총한도 비교는 5단계로 남긴다. Agent 노드/prompt/모델 호출 수는 바꾸지 않았다.

## 제한과 후속

- 4단계 Worker 전용 깨우기 신호·재연결·fan-out·빈 claim 최적화는 미구현이다. 현재는 polling으로 재확인한다.
- 구·신 dispatcher의 롤링 혼재는 지원하지 않는다. 별도 event DB의 실제 이관·권한·운영 전환은 미수행이다.
- 기존 Redis dispatcher/guard/Outbox 파일과 관련 회귀 삭제는 자동 승인 검토가 안전장치 제거로 거절해 보류했다. 파일은 유지하며 새 bootstrap에서 미사용이다. 삭제를 완료한 것으로 표시하지 않는다.
- legacy retry/skip 도구는 공통 원장 복구 도구가 아니다. RECOVERY 자동 탈취·광범위 관리자 복구 API는 추가하지 않았다.
- 057 성능 runner는 이전 두 실행기 capture에 맞춰져 있어 현재 구조의 잘못된 한도 비교를 거절한다. 공유 한도 성능 harness는 5단계에서 업데이트한다.

원본 checkout, 기존 .env·서비스 컨테이너·실제 DB·Redis group은 변경하지 않았다. 구현은 파생 브랜치에서 commit하며 사용자 요청 전 베이스 merge/push는 하지 않는다.
