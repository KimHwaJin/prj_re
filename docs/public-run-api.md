# 공개 Run API 계약

019 구현 · 2026-09-29

`run_id`(응답 필드 `id`)는 최초 접수부터 여러 HITL과 Executor 최종 결과까지 유지한다. 내부 `agent_runs.run_id`는 실행 구간 ID이며, 공개 ID는 `agent_runs.public_run_id`다. 초기 구간에서는 두 값이 같다. 프론트는 `task_id`나 `checkpoint_run_id`로 재개 대상을 조립하지 않는다.

## 호출 순서

모든 호출에 등록된 사용자의 `X-User-Id`를 보낸다. 최초 생성과 각 재개 명령에 `Idempotency-Key`를 보낸다. 한 명령을 네트워크 오류로 재전송할 때는 키와 body를 모두 유지하며, 다음 승인 응답에는 새 키를 사용한다.

1. `POST /api/v1/sessions/{session_id}/runs`

```json
{"input":{"messages":[{"role":"user","content":"불량 예측 분석"}]}}
```

202와 `id=R`, `status=pending`, `Location=/api/v1/sessions/{session_id}/runs/R`를 받는다.

2. `GET /api/v1/sessions/{session_id}/runs/R`

```json
{
  "id":"R",
  "status":"waiting_input",
  "resume_token":"현재 승인 대기에서 받은 UUID",
  "interrupt":[{"kind":"USER_APPROVAL"}],
  "completed_at":null
}
```

예시는 설명용 발췌다. 실제 ID/token은 UUID이며 다른 timestamp·진단 필드도 응답한다. `resume_token`은 상태에서 받은 값을 그대로 돌려주는 승인 단계 식별자다. 인증 수단이 아니며 다음 승인 단계에서 바뀐다.

3. `POST /api/v1/sessions/{session_id}/runs/R/resume`

```json
{"command":{"approved":true},"resume_token":"조회에서 받은 UUID"}
```

202 응답의 `id`와 Location은 계속 R이다. 현재 승인이 끝났거나 다른 단계의 token이면 409다. 동일 키·동일 body 재전송은 새 실행을 만들지 않고 해당 공개 Run의 **현재 상태**를 반환한다. 처음 접수 당시의 응답 snapshot을 고정 재생하는 방식은 아니다. 같은 키를 다른 body/다른 Run에 재사용하면 409다. 마이그레이션 이전의 resume 키는 새 token 계약과 동일한 명령인지 증명할 수 없어 재사용을 거절한다. 기존 대기 상태를 조회하고 새 token/body/키로 재개한다.

4. Executor 제출 후 같은 GET에 `waiting_executor`가 표시된다. `resume_token=null`이며 사용자 resume와 같은 세션의 새 Run은 거절한다. 외부 완료 이벤트가 반영되면 같은 R이 `success/error/canceled` 등 최종 상태로 바뀌고 `result`에 Agent 응답이 담긴다. `completed_at`은 전체 작업이 끝났을 때만 설정된다.

## 공개 상태

| 상태 | 의미 | 사용자 동작 |
|---|---|---|
| pending | 실행 큐/확인된 실패 재시도 대기 | 대기, 취소 요청 |
| running | Agent 실행 중 | 대기, 취소 요청 |
| waiting_input | 현재 HITL 응답 대기 | token과 함께 resume 또는 취소 |
| waiting_executor | 장기 Executor 결과 대기 | 결과 대기; 사용자 resume 불가 |
| recovery_required | 이전 실행 종료/소유권 확인 필요 | 자동 재실행·세션 해제 불가 |
| success/error/timeout/canceled | 전체 작업 종료 | 같은 세션에서 새 Run 가능 |

`attempt_count`/`next_attempt_at`는 현재 내부 실행 구간의 재시도 진단값이다. 전체 구간 수가 아니다. Task/checkpoint 진단 필드는 기존 연계 확인용으로 남겨 두지만 프론트 제어에 필요하지 않다. 기존 내부 Run URL의 GET/join은 공개 루트의 최신 상태를 반환하는 별칭이다.

## 목록·로그·이벤트

- `GET .../runs`: 내부 구간마다 항목을 만들지 않고 논리 Run당 하나. 최초 접수 시간 기준으로 기존 cursor pagination을 사용한다.
- `GET .../runs/R/logs`: 모든 구간의 로그를 모아 반환하며 응답 `run_id`는 R로 정규화한다. 기존 log_id와 저장된 내부 구간 관계는 보존한다.
- `GET .../runs/R/join`: 기존과 동일하게 즉시 상태 조회하는 별칭이다. 완료까지 HTTP 요청을 붙잡는 기능은 추가하지 않았다.
- `GET .../runs/R/stream`: 기존 SSE를 전체 Run 범위로 연결한다. HITL에서도 연결을 유지할 수 있고 재접속 시 같은 URL을 사용한다. `Last-Event-ID`는 durable Task 이벤트의 단조 증가 sequence다. 구간을 넘겨 재생하며 최종 상태와 남은 이벤트를 전달한 후 닫는다.
- SSE `run.state`는 현재 GET과 같은 상태 snapshot이고 durable ID를 발행하지 않는다. 클라이언트는 durable 이벤트의 마지막 ID를 보관한다. backlog를 먼저 재생한 뒤 상태 snapshot을 전달한다. 하위 호환을 위해 기존 `task.*` 이벤트명/구간 상태 payload는 유지하며 프론트의 전체 실행 상태는 `run.state`를 사용한다.
- SSE 구현은 여전히 서버 내부 DB 폴링이다. PostgreSQL LISTEN/NOTIFY 전환이나 폴링 부하 개선을 이번 성과로 주장하지 않는다. 연결 대기/yield 동안 DB session은 닫혀 있다.

## 취소

`POST .../runs/R/cancel`은 최신 내부 구간/Task에 적용한다. 실행 중이면 실제 중단 확인까지 상태·세션 보호를 유지한다. 사용자 응답 대기 중 취소는 즉시 종료하며 반복 취소는 같은 canceled 상태를 반환한다.

Executor에 이미 제출한 작업은 외부 취소 확인 계약을 구현하지 않았으므로 cancel에 409를 반환한다. 로컬 Task만 취소하여 외부 작업이 살아 있는 세션을 해제하지 않는다. 이 제한은 deprecated Task cancel에도 동일하다.

## 기존 클라이언트 변경

- `POST .../runs`의 command/metadata.resume_run_id 재개 방식은 종료한다. `/runs/R/resume`과 `resume_token`으로 바꾼다.
- 공개 상태 `interrupted` 검사는 `waiting_input`/`waiting_executor`로 분리한다.
- Tasks 라우트는 deprecated로 남겨 둔다. Task resume도 동일한 token을 받아 공통 공개 Run 접수 경로를 사용한다. Tasks 전체 삭제/공개 API 정리는 다음 작업이다.
- 현재 공유 부하 시나리오 `scripts/loadtest/scenario.py`는 새 계약을 사용한다. 사전 생성된 테스트 관리자를 `DTEST_LOADTEST_ADMIN_USER_ID`로 지정한다. 과거 진단/벤치마크는 저장된 정확한 이전 commit을 비교하는 자료이므로 일괄 치환하지 않는다.
- 내부 데모 UI의 전면 연계 수정은 이번 범위가 아니다.

## 배포와 데이터 이관

1. API·Run Worker·Executor 이벤트 Worker를 drain/중지한다. 종료 불명 작업의 자동 재실행은 금지한다.
2. DB backup 후 동일 설정 snapshot으로 `python -m alembic -c alembic.crud.ini upgrade head`를 실행한다.
3. 0021은 기존 Task root 또는 같은 세션의 유효한 checkpoint root로 public_run_id를 채운다. 잘못된 UUID나 타 세션의 metadata 참조는 연결 근거로 사용하지 않는다. 모호한 비루트 연결이나 같은 공개 ID에 서로 다른 Task/event sequence가 존재하면 명시적으로 실패하므로 데이터를 확인한 뒤 재실행한다.
4. 새 코드만 기동하고 알려진 기존 Run/대기 상태를 GET으로 검증한다. 구 코드와 신 코드의 혼합 writer 배포는 지원하지 않는다.

기존 내부 Run/Task/메시지/log/체크포인트/Executor binding ID는 바꾸지 않는다. 체크포인트 테이블 migration은 추가하지 않는다. 0021 downgrade는 새 컬럼/인덱스/FK만 제거하며 기존 행을 합치거나 삭제하지 않는다. 외부 대기는 DB 행과 체크포인트에 보존되고 실행 슬롯/DB 연결을 유지하지 않는다.
