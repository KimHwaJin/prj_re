# 사용자 resume와 checkpoint 복구 계약

사용자 입력과 재개는 모두 `POST /api/v1/sessions/{session_id}/runs`로 접수한다.
재개 body에는 공개 `run_id`, 최신 `resume_token`, `command.resume`을 넣는다.
별도 `/runs/{run_id}/resume` endpoint는 없다. 인증은 SSO 쿠키와 변경 요청의
CSRF다. 실제 body·응답·SSE는 [공개 Run API](../public-run-api.md)를 따른다.

## 식별자와 영속 기록

공개 Run ID는 전체 업무에서 유지한다. 재개마다 private invocation을 기록하고
공통 `agent_commands` 원장에 실행 명령을 접수한다. 명령 원장이 없다는 옛030
설명은 현재 구조에 적용되지 않는다. [공통 Worker](../agent-command-worker.md)를
함께 참고한다.

- 대기 결과의 `metadata._checkpoint_interrupt_id`는 실제 interrupt 대상이다.
- 재개 접수 시 `_resume_target`에 대상을 고정한다. 동일 명령의 재시도에서
  최신 질문을 다시 골라 보내지 않는다.
- 호출 직전 `_resume_started=true`를 짧은 transaction으로 저장한다. 이 값만으로
  사용자 입력이 소비됐다고 판단하지 않는다.
- 성공한 HITL 노드의 상태 변경과 `user_resume_receipt`가 같은 checkpoint에
  저장된다. receipt는 private invocation ID, interrupt ID와 입력 digest다.
- 프론트는 내부 command ID나 checkpoint interrupt ID를 직접 만들지 않는다.
  서버가 내려준 공개 interaction/token을 사용한다.

## 호출과 복구

현재 구현은 [user_resume protocol](../../src/dtest/application/runs/protocols/user_resume.py),
[공통 projection 복구](../../src/dtest/application/runs/persistence/recovery.py)에 있다.

1. 세션 실행 점유 아래 checkpoint·모델 선택을 검사한다.
2. 동일 private invocation의 receipt가 있으면 대상·digest와 다음 대기/종료 상태를
   확인하고 graph를 다시 호출하지 않고 서비스 DB projection만 재실행한다.
3. 아직 호출하지 않은 명령은 유일한 interrupt가 고정 대상인지 확인하고
   started 표시를 commit한 뒤 해당 interrupt로 resume한다.
4. 호출 후 receipt와 진행 상태를 검사한다. 모델/Executor를 포함한 graph 전체
   종료를 receipt 하나로 판단하지 않는다.
5. 호출 표시가 있는데 receipt가 없거나, 대상이 다르거나, 진행이 불완전하면
   자동 재제출하지 않고 `UserResumeNeedsRecovery`로 보호한다.

checkpoint가 완료되어도 서비스 DB 반영은 별도로 실패할 수 있다. 이 경우
`GraphProjectionError`로 projection을 재시도한다. graph 입력이 소비되었는지
불확실한 실패와 구분한다. 재시도 소진·점유 불확실성 처리도 현재
[명령 outcome](../../src/dtest/application/runs/commands/outcome.py)을 따른다.
receipt는 외부 HTTP 부작용까지 포함한 exactly-once 보장이 아니다.

## Agent 개발 규칙

[공용 계약](../../src/dtest/contracts/user_resume.py)과
[HITL helper](../../src/dtest/agent_service/runtime/user_resume.py)를 사용한다.
Agent가 API·DB 구현을 import하지 않는다.

```python
from dtest.agent_service.runtime.user_resume import (
    record_user_resume,
    user_interrupt,
)
from dtest.contracts.user_resume import UserResumeState


class State(UserResumeState, total=False):
    answer: str


@record_user_resume
async def ask(state: State) -> dict:
    answer = user_interrupt({"kind": "question"})
    return {"answer": answer}
```

한 노드는 사용자 응답 하나를 소비하고 dict 상태 변경을 반환한다. 노드가 실패하거나
다시 interrupt하면 완료 receipt를 만들지 않는다. state를 직접 바꾸거나
user_resume_receipt를 업무 코드에서 덮어쓰지 않는다. 현재 분석 graph는
`request_human_input` 노드에서 이 helper를 사용한다. 과거 데이터 선택 전용
노드 목록을 현재 구현으로 나열하지 않는다.

직접 LangGraph를 사용하는 개발 도구는 raw resume도 가능하지만 서버 envelope를
사용하는 서비스의 복구 보호 계약과 동일하다고 가정하지 않는다. 배포 시 구·신
writer 혼재와 이미 진행 중인 작업은 별도 전환·종료·검증이 필요하다.
